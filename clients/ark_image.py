#!/usr/bin/env python3
"""Seedream 图像生成客户端 —— 零第三方依赖（仅标准库）。

实测结论（2026-10-30，本机验证）：
  · 端点 POST /api/v3/images/generations，鉴权 Authorization: Bearer <ARK_API_KEY>
  · size 接受显式 "1024x1536"，返回精确 1024x1536（比例 0.6667 = 真 2:3）
  · 返回体 data[0].url，需立刻下载（预签名 URL 会过期）
  · 1024x1536 png 单张约 2.7MB、耗时 ~31.5s，usage.generated_images=1
  · watermark=true 时右下角带「AI 生成」标识 —— 合规要求，不要关

v1.3 变更（2026-10-30）：
  · 生成尺寸固定为 9:16 竖屏，不再"先出 2:3 再裁" —— 裁切会丢边、且每次都损画质
  · 新增 generate_vertical()：按尺寸阶梯降级，保证永远拿到 9:16 原生画面
  · refs 支持本地文件：自动缩到 1080x1920 JPEG 再转 data-URI（原图 2.7MB 直接
    base64 会把请求撑到 3.6MB，容易被网关拒）

设计要点：
  · 网络错误分类重试：429/5xx/超时 重试，400/401/内容审核 不重试（省冤枉钱）
  · 内容寻址缓存：同 prompt+size+refs 命中即复用，0 成本
  · 记账：每次真实调用写一行 JSONL，供 costs 表汇总
"""
import base64
import hashlib
import json
import os
import time
import urllib.error
import urllib.request

ARK_HOST = "https://ark.cn-beijing.volces.com/api/v3"
DEFAULT_MODEL = "doubao-seedream-5-0-pro-260628"
FALLBACK_MODEL = "doubao-seedream-5-0-flash-260915"
RETRY_STATUS = {408, 429, 500, 502, 503, 504}
# 这些状态重试无意义：参数错、鉴权错、被审核拒绝、模型未开通
FATAL_STATUS = {400, 401, 403, 404, 422}

# 交付规格：成片 1080x1920 = 9:16
CANVAS_W, CANVAS_H = 1080, 1920
# 生成尺寸阶梯，全部是精确 9:16，从首选到兜底。
# 首选与成片同尺寸 → 零缩放零裁切，锐度最好。
SIZES_9X16 = ["1080x1920", "1152x2048", "864x1536"]


class ArkImageError(Exception):
    def __init__(self, status, message, body=None, retryable=False):
        super().__init__(f"[{status}] {message}")
        self.status = status
        self.message = message
        self.body = body or {}
        self.retryable = retryable

    @property
    def is_content_reject(self):
        text = json.dumps(self.body, ensure_ascii=False).lower()
        return any(k in text for k in ("sensitive", "content_policy", "risk", "审核", "违规"))

    @property
    def is_model_not_open(self):
        return "ModelNotOpen" in json.dumps(self.body, ensure_ascii=False)


def image_dims(raw):
    """零依赖读宽高：PNG IHDR / JPEG SOF。"""
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        return ("png", int.from_bytes(raw[16:20], "big"), int.from_bytes(raw[20:24], "big"))
    if raw[:2] == b"\xff\xd8":
        i = 2
        while i < len(raw) - 9:
            if raw[i] != 0xFF:
                i += 1
                continue
            m = raw[i + 1]
            if m in (0xC0, 0xC1, 0xC2, 0xC3):
                return ("jpeg", int.from_bytes(raw[i + 7:i + 9], "big"),
                        int.from_bytes(raw[i + 5:i + 7], "big"))
            if m in (0xD8, 0xD9) or 0xD0 <= m <= 0xD7:
                i += 2
                continue
            i += 2 + int.from_bytes(raw[i + 2:i + 4], "big")
    return ("unknown", 0, 0)


class ArkImage:
    def __init__(self, api_key, model=DEFAULT_MODEL, fallback_model=FALLBACK_MODEL,
                 cache_dir=None, cost_log=None, timeout=240, unit_price=None):
        if not api_key:
            raise ArkImageError(401, "ARK_API_KEY 未配置（方舟控制台 → API Key）")
        self.api_key = api_key
        self.model = model
        self.fallback_model = fallback_model
        self.cache_dir = cache_dir or os.path.join(os.getcwd(), "runs", "_cache")
        self.cost_log = cost_log or os.path.join(self.cache_dir, "costs.jsonl")
        self.timeout = timeout
        self.unit_price = unit_price          # 元/张，None 则只记张数不记金额
        os.makedirs(self.cache_dir, exist_ok=True)

    # ------------------------------------------------------------ 缓存
    def cache_key(self, prompt, size, refs=None, output_format="png", fidelity=None):
        refs = refs or []
        ref_sig = "|".join(
            hashlib.sha1(open(r, "rb").read()).hexdigest() if os.path.exists(r) else str(r)
            for r in refs
        )
        raw = f"{self.model}|{size}|{output_format}|{fidelity}|{prompt}|{ref_sig}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    def _cache_paths(self, key):
        return (os.path.join(self.cache_dir, key + ".png"),
                os.path.join(self.cache_dir, key + ".json"))

    # ------------------------------------------------------------ 主流程
    def generate(self, prompt, out_path=None, size="1080x1920", refs=None,
                 output_format="png", allow_fallback=True, fidelity=None):
        """生成一张图。返回 dict：path/cached/w/h/ms/usage/degrade/bytes。"""
        key = self.cache_key(prompt, size, refs, output_format, fidelity)
        img_path, meta_path = self._cache_paths(key)

        if os.path.exists(img_path) and os.path.getsize(img_path) > 1024:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
            meta.update(cached=True, path=self._place(img_path, out_path))
            return meta

        last = None
        try:
            meta = self._call_and_save(prompt, size, refs, output_format, img_path,
                                       fidelity=fidelity)
        except ArkImageError as e:
            last = e
            # 模型未开通或内容被拒时，换降级模型通常也没用；只在超时/限流时降级
            if not allow_fallback or e.is_content_reject or e.is_model_not_open:
                raise
            meta = self._call_and_save(prompt, size, refs, output_format,
                                       img_path, model=self.fallback_model,
                                       fidelity=fidelity)
            meta["degrade"] = "fallback_model"

        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        meta["path"] = self._place(img_path, out_path)
        meta["cached"] = False
        meta.setdefault("degrade", None)
        if last is not None:
            meta["first_error"] = str(last)
        return meta

    def _place(self, cache_path, out_path):
        if not out_path:
            return cache_path
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        if os.path.abspath(out_path) != os.path.abspath(cache_path):
            with open(cache_path, "rb") as src, open(out_path, "wb") as dst:
                dst.write(src.read())
        return out_path

    def _call_and_save(self, prompt, size, refs, output_format, img_path,
                       model=None, fidelity=None):
        payload = {
            "model": model or self.model,
            "prompt": prompt,
            "size": size,
            "output_format": output_format,
            "watermark": True,       # 合规标识：保留，勿关
        }
        if refs:
            uris = [self._as_data_uri(r) for r in refs]
            payload["image"] = uris[0] if len(uris) == 1 else uris
            if fidelity:
                payload["input_fidelity"] = fidelity

        body, ms = self._request(payload)
        items = body.get("data") or []
        if not items:
            raise ArkImageError(200, "响应缺少 data[]", body)
        raw = self._materialize(items[0])
        fmt, w, h = image_dims(raw)
        with open(img_path, "wb") as f:
            f.write(raw)
        meta = {
            "model": body.get("model", payload["model"]),
            "size_arg": size, "w": w, "h": h,
            "ratio": round(w / h, 4) if h else None,
            "format": fmt, "bytes": len(raw), "ms": ms,
            "usage": body.get("usage"), "refs": len(refs or []),
        }
        self._bill(meta)
        return meta

    # ------------------------------------------------------------ 9:16
    def generate_vertical(self, prompt, out_path=None, refs=None, fidelity=None,
                          sizes=None):
        """固定 9:16 竖屏。按尺寸阶梯降级，直到拿到原生竖图。

        返回的 meta 里 size_arg / w / h / ratio / size_degrade 记录了实际落点，
        便于事后定位"哪一张是靠降级才出来的"。
        """
        errors = []
        for i, size in enumerate(sizes or SIZES_9X16):
            try:
                meta = self.generate(prompt, out_path=out_path, size=size,
                                     refs=refs, fidelity=fidelity,
                                     allow_fallback=True)
                meta["size_degrade"] = None if i == 0 else f"sizes[{i}]={size}"
                if abs((meta.get("w") or 0) / max(meta.get("h") or 1, 1) - 0.5625) > 0.01:
                    errors.append(f"{size}: 返回比例 {meta.get('ratio')} 非 9:16")
                    continue
                return meta
            except ArkImageError as e:
                # 400 参数不接受 / 尺寸越界 → 换下一档；其余（401/审核/未开通）直接抛
                if e.status == 400:
                    errors.append(f"{size}: {e.message[:80]}")
                    continue
                raise
        raise ArkImageError(400, "所有 9:16 尺寸均被拒：" + " | ".join(errors))

    def _as_data_uri(self, ref):
        """本地图 → data-URI。先缩到 1080x1920 JPEG q4，避免请求体过大。"""
        if not os.path.exists(ref):
            return ref                      # 已是 http(s) 或 data-URI
        thumb_dir = os.path.join(self.cache_dir, "_ref")
        os.makedirs(thumb_dir, exist_ok=True)
        dst = os.path.join(thumb_dir,
                           hashlib.sha1(os.path.abspath(ref).encode()).hexdigest()[:16] + ".jpg")
        if not os.path.exists(dst):
            import subprocess
            subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-i", ref,
                 "-vf", f"scale={CANVAS_W}:{CANVAS_H}:force_original_aspect_ratio=increase,"
                        f"crop={CANVAS_W}:{CANVAS_H}",
                 "-q:v", "4", dst],
                check=True, capture_output=True)
        with open(dst, "rb") as f:
            return "data:image/jpeg;base64," + base64.b64encode(f.read()).decode()

    def _materialize(self, item):
        if item.get("b64_json"):
            return base64.b64decode(item["b64_json"])
        if item.get("url"):
            with urllib.request.urlopen(item["url"], timeout=120) as r:
                return r.read()
        raise ArkImageError(200, "无法识别返回字段：" + ",".join(item.keys()), item)

    # ------------------------------------------------------------ HTTP
    def _request(self, payload, max_retries=3):
        data = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json",
                   "Authorization": "Bearer " + self.api_key}
        delay = 2.0
        for attempt in range(max_retries + 1):
            t0 = time.time()
            try:
                req = urllib.request.Request(ARK_HOST + "/images/generations",
                                             data=data, headers=headers, method="POST")
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    return json.loads(r.read().decode("utf-8")), int((time.time() - t0) * 1000)
            except urllib.error.HTTPError as e:
                body_text = e.read().decode("utf-8", "replace")
                try:
                    body = json.loads(body_text)
                except Exception:
                    body = {"raw": body_text[:500]}
                err = ArkImageError(e.code, _msg(body), body,
                                    retryable=e.code in RETRY_STATUS)
                if not err.retryable or attempt == max_retries:
                    raise err
            except Exception as e:  # 超时 / DNS / 连接重置
                err = ArkImageError(0, f"{type(e).__name__}: {e}", retryable=True)
                if attempt == max_retries:
                    raise err
            time.sleep(delay)
            delay *= 3
        raise ArkImageError(0, "重试耗尽")

    def _bill(self, meta):
        rec = {
            "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "stage": "image", "provider": "ark", "model": meta["model"],
            "units": (meta.get("usage") or {}).get("generated_images", 1),
            "unit_type": "image", "latency_ms": meta["ms"],
            "amount_cny": None if self.unit_price is None
                          else round(self.unit_price * (meta.get("usage") or {}).get("generated_images", 1), 4),
        }
        with open(self.cost_log, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _msg(body):
    if isinstance(body, dict):
        e = body.get("error") if isinstance(body.get("error"), dict) else body
        return e.get("message") or json.dumps(body, ensure_ascii=False)[:300]
    return str(body)[:300]
