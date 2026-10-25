"""复用课程 Canny/Hough、Lanczos 和对比度思路，并记录可逆坐标变换。"""
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

from ..infrastructure.storage import atomic_json, digest, file_hash, read_json
from ..schemas import PipelineError


def pdf_orientation(page, opts):
    """按字符数加权的文字方向投票；先计入 PDF 自带旋转，避免重复转正。"""
    if not opts.get("pdf_auto_orient", False):
        return {"clockwise_degrees": 0, "reason": "disabled"}
    weights = {0: 0, 90: 0, 180: 0, 270: 0}
    total = 0
    matrix = page.rotation_matrix
    for block in page.get_text("dict").get("blocks", []):
        for line in block.get("lines", []):
            weight = sum(len(s.get("text", "").strip()) for s in line.get("spans", []))
            if not weight:
                continue
            total += weight
            dx, dy = line["dir"]
            x, y = dx * matrix.a + dy * matrix.c, dx * matrix.b + dy * matrix.d
            angle = math.degrees(math.atan2(y, x))
            nearest = round(angle / 90) * 90
            if abs(angle - nearest) <= 10:
                weights[(-nearest) % 360] += weight
    turn = max(weights, key=weights.get)
    confidence = weights[turn] / total if total else 0
    accepted = total >= opts.get("orientation_min_chars", 50) and confidence >= opts.get("orientation_min_ratio", .9)
    return {"clockwise_degrees": turn if accepted else 0, "text_chars": total,
            "confidence": confidence, "pdf_rotation": page.rotation,
            "reason": "dominant_text" if accepted else "insufficient_or_mixed_text"}


def rotate_quadrant(image, degrees):
    """返回转正图及从转正图映回原栅格的矩阵（像素边界坐标）。"""
    w, h = image.size
    if degrees == 90:
        return image.transpose(Image.Transpose.ROTATE_270), np.array([[0, 1, 0], [-1, 0, h], [0, 0, 1]])
    if degrees == 180:
        return image.transpose(Image.Transpose.ROTATE_180), np.array([[-1, 0, w], [0, -1, h], [0, 0, 1]])
    if degrees == 270:
        return image.transpose(Image.Transpose.ROTATE_90), np.array([[0, -1, w], [1, 0, 0], [0, 0, 1]])
    if degrees != 0:
        raise PipelineError("方向纠正仅支持 0/90/180/270 度")
    return image, np.eye(3)


def preprocess(image, cfg):
    """坐标基准是 EXIF 校正后的原始栅格，矩阵将处理图坐标映回该栅格。"""
    import cv2
    image = image.convert("RGB")
    forward = np.eye(3)
    actions = []
    if cfg["deskew"]:
        gray = np.asarray(image.convert("L"))
        scale = min(1, 1600 / max(image.size))
        small = cv2.resize(gray, None, fx=scale, fy=scale) if scale < 1 else gray
        edges = cv2.Canny(small, 50, 150)
        lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=40,
                               minLineLength=max(40, min(small.shape) // 6), maxLineGap=12)
        pairs = []
        if lines is not None:
            for x1, y1, x2, y2 in lines[:, 0]:
                angle = math.degrees(math.atan2(float(y2 - y1), float(x2 - x1)))
                angle = (angle + 90) % 180 - 90
                if abs(angle) <= 12:
                    pairs.append((angle, math.hypot(float(x2 - x1), float(y2 - y1))))
        if len(pairs) >= 3:
            angle = float(np.average([p[0] for p in pairs], weights=[p[1] for p in pairs]))
            if 0.7 <= abs(angle) <= 12:
                w, h = image.size
                # 图像 y 轴向下；OpenCV 正角度旋转可消去检测到的正斜率。
                rotation = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1)
                cosine, sine = abs(rotation[0, 0]), abs(rotation[0, 1])
                nw, nh = math.ceil(h * sine + w * cosine), math.ceil(h * cosine + w * sine)
                rotation[:, 2] += [(nw - w) / 2, (nh - h) / 2]
                if nw * nh <= cfg["max_pixels"]:
                    image = Image.fromarray(cv2.warpAffine(np.asarray(image), rotation, (nw, nh), borderValue=(255, 255, 255)))
                    forward = np.vstack([rotation, [0, 0, 1]]) @ forward
                    actions.append({"deskew_degrees": angle})
    scale = min(cfg["max_scale"], max(1, cfg["min_side"] / min(image.size)))
    scale = min(scale, math.sqrt(cfg["max_pixels"] / (image.width * image.height)))
    if scale > 1:
        old = image.size
        image = image.resize((round(old[0] * scale), round(old[1] * scale)), Image.Resampling.LANCZOS)
        forward = np.diag([image.width / old[0], image.height / old[1], 1]) @ forward
        actions.append({"upscale": scale})
    gray = np.asarray(image.convert("L"))
    lo, hi = np.percentile(gray, [2, 98])
    if hi - lo < 100 or float(gray.std()) < 35:
        image = ImageOps.autocontrast(image, cutoff=1)
        actions.append({"autocontrast": True})
    # 页面不二值化/裁白边，保留原色负数和脚注；目标表格在下一阶段裁剪。
    return image, np.linalg.inv(forward).tolist(), actions


def prepare_document(source, cfg, trace):
    source = Path(source)
    opts = cfg["processing"]
    key = digest({"source": file_hash(source), "options": opts, "code": file_hash(__file__)})
    folder = Path(cfg["paths"]["cache"]) / "documents" / key
    manifest = folder / "pages.json"
    with trace.stage("document", {"file": source.name, "fingerprint": key}) as output:
        if manifest.exists():
            pages = read_json(manifest)
            if all(Path(p["image"]).exists() and Path(p["original"]).exists() for p in pages):
                output["cached"] = True
                return pages
        folder.mkdir(parents=True, exist_ok=True)
        pages = []

        def save(image, number, metadata):
            if image.width * image.height > opts["max_pixels"]:
                raise PipelineError("页面像素超过 max_pixels，需降低 DPI 或调整预算")
            original = folder / f"p{number:04d}-original.png"
            target = folder / f"p{number:04d}.png"
            image = image.convert("RGB")
            image.save(original)
            degrees = metadata.get("orientation", {}).get("clockwise_degrees", 0)
            image, orientation_inverse = rotate_quadrant(image, degrees)
            processed, inverse, actions = preprocess(image, opts) if opts["preprocess"] else (image, np.eye(3).tolist(), [])
            inverse = (orientation_inverse @ np.asarray(inverse)).tolist()
            if degrees:
                actions.insert(0, {"orientation_clockwise_degrees": degrees})
            processed.save(target)
            pages.append({"page": number, "image": str(target), "original": str(original),
                          "width": processed.width, "height": processed.height,
                          "processed_to_original": inverse, "actions": actions, **metadata})

        if source.suffix.lower() == ".pdf":
            import pymupdf
            with pymupdf.open(source) as doc:
                if len(doc) > opts["max_pages"] or not len(doc):
                    raise PipelineError("PDF 页数为空或超过 max_pages")
                for i, page in enumerate(doc, 1):
                    zoom = opts["dpi"] / 72
                    if math.ceil(page.rect.width * zoom) * math.ceil(page.rect.height * zoom) > opts["max_pixels"]:
                        raise PipelineError("PDF 页面渲染尺寸超过 max_pixels")
                    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False, colorspace=pymupdf.csRGB)
                    orientation = pdf_orientation(page, opts)
                    trace.emit("page_orientation", page=i, **orientation)
                    save(Image.frombytes("RGB", (pix.width, pix.height), pix.samples), i,
                         {"dpi": opts["dpi"], "orientation": orientation})
        else:
            with Image.open(source) as image:
                if image.width * image.height > opts["max_pixels"]:
                    raise PipelineError("原图尺寸超过 max_pixels")
                orientation = image.getexif().get(274, 1)
                save(ImageOps.exif_transpose(image), 1, {"exif_orientation": orientation})
        atomic_json(manifest, pages)
        output["pages"] = len(pages)
        return pages


def crop_image(source, area, target, zoom=1, max_pixels=40000000):
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as image:
        cut = image.crop(tuple(area)).convert("RGB")
        scale = min(zoom, math.sqrt(max_pixels / (cut.width * cut.height)))
        if scale > 1:
            cut = cut.resize((round(cut.width * scale), round(cut.height * scale)), Image.Resampling.LANCZOS)
        cut.save(target)
    return str(target)
