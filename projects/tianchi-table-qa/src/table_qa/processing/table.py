"""文档级表格识别：与题目无关，便于同一文档多题复用。"""
from pathlib import Path
from PIL import Image

from ..infrastructure.storage import atomic_json, digest
from ..schemas import PipelineError
from ..validation.table import decode_recognized_table
from ..validation.coordinates import to_pixels, emit_warnings
from .document import crop_image
from .long_table import merge_tables


def _recognize(model, context, image, source, header=None, cell_format="object"):
    paths = [context, image] + ([header] if header else [])
    table = decode_recognized_table(model.ask("recognize", {"source": source, "cell_format": cell_format}, paths))
    for c in table["cells"]:
        c["sources"] = [source]
    table["segments"] = [source]
    return table


def _join(model, left, right, left_image, right_image, required=False):
    if left.get("incomplete") or right.get("incomplete"):
        _mark_incomplete([left, right], "相邻片段已有未解决边界，不继续自动拼接")
        return None
    with Image.open(left_image) as im:
        tail_area = [0, max(0, im.height * 3 // 4), im.width, im.height]
    with Image.open(right_image) as im:
        head_area = [0, 0, im.width, max(1, im.height // 3)]
    tail = crop_image(left_image, tail_area, str(left_image) + ".tail.png")
    head = crop_image(right_image, head_area, str(right_image) + ".head.png")
    try:
        def window(table, end):
            limit = 4
            selected = [c for c in table["cells"] if c["row"] >= max(0, table["row_count"] - limit)] if end else [c for c in table["cells"] if c["row"] < limit]
            return {"row_count": table["row_count"], "col_count": table["col_count"],
                    "cells": [{k: c[k] for k in ("text", "row", "col", "rowspan", "colspan")} for c in selected]}
        relation = model.ask("boundary", {"left": window(left, True), "right": window(right, False)}, [tail, head])
        merged = merge_tables(left, right, relation)
        if merged is None and required:
            raise PipelineError("同表数据块无法确认续接关系")
        return merged
    except PipelineError as exc:
        # 只降级数据/识别错误；预算、认证、取消异常仍向上传播停止运行。
        _mark_incomplete([left, right], str(exc))
        model.trace.emit("table_merge_fallback", required=required, error=str(exc))
        return None


def _mark_incomplete(tables, reason):
    for table in tables:
        table["incomplete"] = True
        table["integrity_warnings"] = list(dict.fromkeys(table.get("integrity_warnings", []) + [reason]))


def recognize_tables(pages, model, cfg, work):
    folder = Path(work) / "tables"
    opts = {**cfg["processing"], "block_height": cfg["run"].get("recognition_block_height", cfg["processing"]["block_height"])}
    tables = []
    previous_image = None
    previous_page = None
    recognition_errors = []
    for page in pages:
        locations = model.ask("locate", {"width": page["width"], "height": page["height"]}, [page["image"]])
        locations = locations.get("tables")
        if not isinstance(locations, list) or len(locations) > opts["max_tables_per_page"]:
            raise PipelineError("表格定位返回非法列表或数量超限")
        warnings, normalized = [], []
        try:
            for region in locations:
                area = to_pixels(region.get("bbox"), page["width"], page["height"], cfg, warnings)
                head = None
                if region.get("header_bbox") is not None:
                    try:
                        head = to_pixels(region["header_bbox"], page["width"], page["height"], cfg, warnings)
                        if not (area[0] <= head[0] < head[2] <= area[2] and area[1] <= head[1] < head[3] <= area[3]):
                            raise PipelineError("表头不在目标表格范围内")
                    except PipelineError as exc:
                        warnings.append(f"表头裁剪取消: {exc}")
                        head = None
                normalized.append({"bbox": area, "header_bbox": head})
        except (PipelineError, AttributeError) as exc:
            warnings.append(f"定位坐标无效，回退整页识别（多表可能无法分离）: {exc}")
            normalized = [{"bbox": [0, 0, page["width"], page["height"]], "header_bbox": None}]
        emit_warnings(model, "locate", warnings)
        for index, region in enumerate(normalized):
            area = region["bbox"]
            tag = f"p{page['page']}-t{index}"
            image = crop_image(page["image"], area, folder / f"{tag}.png")
            source = {"page": page["page"], "bbox": area, "image": image}
            header_image = None
            if region.get("header_bbox") is not None:
                head = region["header_bbox"]
                header_image = crop_image(page["image"], head, folder / f"{tag}-header.png")
            w, h = area[2] - area[0], area[3] - area[1]
            if h > opts["block_height"]:
                raw_blocks = model.ask("blocks", {"width": w, "height": h,
                                      "suggested_height": opts["block_height"]}, [image]).get("blocks")
                if not isinstance(raw_blocks, list) or not 1 <= len(raw_blocks) <= opts["max_blocks"]:
                    raise PipelineError("分块数量为空或超过限制")
                block_warnings = []
                try:
                    blocks = [to_pixels(b, w, h, cfg, block_warnings) for b in raw_blocks]
                except PipelineError as exc:
                    block_warnings.append(f"分块坐标无效，回退整表: {exc}")
                    blocks = [[0, 0, w, h]]
                emit_warnings(model, "blocks", block_warnings)
                end = 0
                for i, b in enumerate(blocks):
                    if b[0] != 0 or b[2] != w or b[1] > end or (i == 0 and b[1] != 0) or b[3] <= end:
                        raise PipelineError("分块没有按顺序覆盖完整表格")
                    end = b[3]
                if end != h:
                    raise PipelineError("分块遗漏表格尾部")
                table = None
                last = None
                fragments = []
                for bi, block in enumerate(blocks):
                    cut = crop_image(image, block, folder / f"{tag}-b{bi}.png")
                    segment = {"page": page["page"], "bbox": [area[0] + block[0], area[1] + block[1],
                               area[0] + block[2], area[1] + block[3]], "image": cut}
                    try:
                        current = _recognize(model, page["image"], cut, segment, header_image,
                                             cfg["processing"].get("recognize_cell_format", "object"))
                    except PipelineError as exc:
                        recognition_errors.append(f"{tag}-b{bi}: {exc}")
                        model.trace.emit("table_recognition_failed", page=page["page"], error=str(exc))
                        if table is not None:
                            fragments.append(table)
                            table = None
                        last = None
                        continue
                    merged = _join(model, table, current, last, cut, required=True) if table is not None else current
                    if merged is None:
                        fragments.append(table)
                        table = current
                    else:
                        table = merged
                    last = cut
                if table is not None:
                    fragments.append(table)
                if len(fragments) != 1 or recognition_errors:
                    _mark_incomplete(fragments, "长表未完整拼接；片段可能重叠或缺失，禁止汇总")
                if not fragments:
                    previous_page = previous_image = None
                    continue
                # 已分离的片段不再与其他页自动拼接，保留各自来源和本地行号。
                if len(fragments) > 1 or fragments[0].get("incomplete"):
                    tables.extend(fragments)
                    previous_page = previous_image = None
                    continue
                table = fragments[0]
            else:
                try:
                    table = _recognize(model, page["image"], image, source,
                                       cell_format=cfg["processing"].get("recognize_cell_format", "object"))
                except PipelineError as exc:
                    recognition_errors.append(f"{tag}: {exc}")
                    model.trace.emit("table_recognition_failed", page=page["page"], error=str(exc))
                    previous_page = previous_image = None
                    continue
            # 只对前页最后一表与本页首表尝试续接；同页不同表默认独立。
            if tables and index == 0 and previous_page == page["page"] - 1:
                merged = _join(model, tables[-1], table, previous_image, image)
                if merged is not None:
                    tables[-1] = merged
                else:
                    tables.append(table)
            else:
                tables.append(table)
            previous_image, previous_page = image, page["page"]
    if not tables:
        raise PipelineError("未识别到可用表格")
    if recognition_errors:
        _mark_incomplete(tables, "文档存在识别失败区域: " + "; ".join(recognition_errors))
    for i, table in enumerate(tables):
        table["table_id"] = f"t{i}"
        for c in table["cells"]:
            c["id"] = f"t{i}:r{c['row']}:c{c['col']}"
    atomic_json(folder / "structures.json", tables)
    model.trace.emit("document_tables_ready", table_count=len(tables),
                     incomplete_count=sum(bool(t.get("incomplete")) for t in tables))
    return tables
