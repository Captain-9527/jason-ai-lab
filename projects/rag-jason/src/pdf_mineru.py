# MinerU API本地文件上传解析模块
# 支持批量上传本地PDF，超200页自动拆分
import os
import time
import shutil
import zipfile
import requests
from pathlib import Path
from PyPDF2 import PdfReader, PdfWriter

# MinerU API限制：单文件不超过200页
MAX_PAGES_PER_FILE = 200
# 批量上传单次不超过50个文件
MAX_BATCH_SIZE = 50
# 轮询间隔（秒）
POLL_INTERVAL = 10

BASE_URL = "https://mineru.net/api/v4"


class MinerUParser:
    """MinerU本地PDF上传解析器"""

    def __init__(self):
        self.token = os.getenv("MINERU_API_TOKEN", "")
        if not self.token:
            raise ValueError("请在.env中设置MINERU_API_TOKEN")
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.token}"
        }

    def _get_pdf_page_count(self, pdf_path: str) -> int:
        """获取PDF总页数"""
        reader = PdfReader(pdf_path)
        return len(reader.pages)

    def _split_pdf(self, pdf_path: str, output_dir: str) -> list:
        """
        将超过200页的PDF按MAX_PAGES_PER_FILE拆分为多个小PDF。
        返回拆分后的文件路径列表。
        """
        reader = PdfReader(pdf_path)
        total_pages = len(reader.pages)
        if total_pages <= MAX_PAGES_PER_FILE:
            return [pdf_path]

        base_name = Path(pdf_path).stem
        os.makedirs(output_dir, exist_ok=True)
        split_files = []

        for start in range(0, total_pages, MAX_PAGES_PER_FILE):
            end = min(start + MAX_PAGES_PER_FILE, total_pages)
            writer = PdfWriter()
            for i in range(start, end):
                writer.add_page(reader.pages[i])

            part_name = f"{base_name}_part{start // MAX_PAGES_PER_FILE + 1}.pdf"
            part_path = os.path.join(output_dir, part_name)
            with open(part_path, 'wb') as f:
                writer.write(f)
            split_files.append(part_path)
            print(f"  拆分: {part_name} (第{start + 1}-{end}页)")

        return split_files

    def _apply_upload_urls(self, file_names: list) -> dict:
        """
        批量申请文件上传链接。
        返回: {"batch_id": str, "file_urls": [str]}
        """
        url = f"{BASE_URL}/file-urls/batch"
        files_data = [{"name": name, "data_id": name} for name in file_names]
        data = {
            "files": files_data,
            "model_version": "vlm"
        }
        response = requests.post(url, headers=self.headers, json=data)
        response.raise_for_status()
        result = response.json()
        if result.get("code") != 0:
            raise RuntimeError(f"申请上传链接失败: {result.get('msg')}")
        return result["data"]

    def _upload_file(self, local_path: str, upload_url: str):
        """PUT上传本地文件到预签名URL"""
        with open(local_path, 'rb') as f:
            resp = requests.put(upload_url, data=f)
        if resp.status_code != 200:
            raise RuntimeError(f"文件上传失败: {local_path}, status={resp.status_code}")
        print(f"  上传成功: {Path(local_path).name}")

    def _poll_batch_result(self, batch_id: str, timeout: int = 1800) -> list:
        """
        轮询批量任务结果，直到所有文件处理完成。
        返回: [{"file_name": str, "state": str, "full_zip_url": str}, ...]
        """
        url = f"{BASE_URL}/extract-results/batch/{batch_id}"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.token}"
        }
        start_time = time.time()

        while True:
            if time.time() - start_time > timeout:
                raise TimeoutError(f"批量任务超时(>{timeout}s): batch_id={batch_id}")

            resp = requests.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json().get("data", {})
            results = data.get("extract_result", [])

            all_done = True
            for r in results:
                state = r.get("state", "")
                if state in ("pending", "running"):
                    all_done = False
                    progress = r.get("extract_progress", {})
                    extracted = progress.get("extracted_pages", "?")
                    total = progress.get("total_pages", "?")
                    print(f"  [{r.get('file_name')}] {state}: {extracted}/{total} 页")
                elif state == "done":
                    pass
                else:
                    err = r.get("err_msg", "")
                    print(f"  [{r.get('file_name')}] 状态异常: {state}, 错误: {err}")

            if all_done:
                return results

            time.sleep(POLL_INTERVAL)

    def _download_and_extract(self, zip_url: str, extract_dir: str):
        """下载zip并解压"""
        os.makedirs(extract_dir, exist_ok=True)
        zip_path = os.path.join(extract_dir, "result.zip")
        print(f"  下载: {zip_url}")
        r = requests.get(zip_url, stream=True)
        with open(zip_path, 'wb') as f:
            for chunk in r.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
        with zipfile.ZipFile(zip_path, 'r') as zf:
            zf.extractall(extract_dir)
        os.remove(zip_path)
        print(f"  解压完成: {extract_dir}")

    def _find_markdown_file(self, extract_dir: str) -> str:
        """在解压目录中查找full.md文件"""
        for root, dirs, files in os.walk(extract_dir):
            for fname in files:
                if fname == "full.md":
                    return os.path.join(root, fname)
        # 如果没有full.md，查找任意.md文件
        for root, dirs, files in os.walk(extract_dir):
            for fname in files:
                if fname.endswith(".md"):
                    return os.path.join(root, fname)
        return ""

    def parse_local_pdf(self, pdf_path: str, output_dir: str):
        """
        解析单个本地PDF文件，输出Markdown到output_dir。
        超200页自动拆分后分别上传解析，最后合并为一个Markdown。
        """
        pdf_path = str(pdf_path)
        base_name = Path(pdf_path).stem
        target_md = os.path.join(output_dir, f"{base_name}.md")

        # 检测页数，必要时拆分
        page_count = self._get_pdf_page_count(pdf_path)
        print(f"  PDF页数: {page_count}")

        temp_dir = os.path.join(output_dir, f"_temp_{base_name}")
        os.makedirs(temp_dir, exist_ok=True)

        try:
            if page_count <= MAX_PAGES_PER_FILE:
                split_files = [pdf_path]
            else:
                print(f"  超过{MAX_PAGES_PER_FILE}页，开始拆分...")
                split_files = self._split_pdf(pdf_path, temp_dir)

            # 分批上传（每批最多50个）
            all_md_contents = []
            for batch_start in range(0, len(split_files), MAX_BATCH_SIZE):
                batch_files = split_files[batch_start:batch_start + MAX_BATCH_SIZE]
                file_names = [Path(f).name for f in batch_files]

                # 申请上传链接
                print(f"  申请上传链接: {len(file_names)} 个文件")
                upload_data = self._apply_upload_urls(file_names)
                batch_id = upload_data["batch_id"]
                file_urls = upload_data["file_urls"]

                # 上传文件
                for local_file, upload_url in zip(batch_files, file_urls):
                    self._upload_file(local_file, upload_url)

                # 轮询等待结果
                print(f"  等待解析完成 (batch_id={batch_id})...")
                results = self._poll_batch_result(batch_id)

                # 下载并提取Markdown
                for r in results:
                    if r.get("state") == "done" and r.get("full_zip_url"):
                        extract_sub = os.path.join(temp_dir, r["file_name"].replace(".pdf", ""))
                        self._download_and_extract(r["full_zip_url"], extract_sub)
                        md_file = self._find_markdown_file(extract_sub)
                        if md_file:
                            with open(md_file, 'r', encoding='utf-8') as f:
                                all_md_contents.append(f.read())
                        else:
                            print(f"  警告: 未找到Markdown文件: {r['file_name']}")
                    else:
                        err = r.get("err_msg", "未知错误")
                        print(f"  解析失败: {r.get('file_name')}, 错误: {err}")

            # 合并所有Markdown内容
            if all_md_contents:
                merged = "\n\n---\n\n".join(all_md_contents)
                with open(target_md, 'w', encoding='utf-8') as f:
                    f.write(merged)
                print(f"  Markdown已保存: {target_md}")
            else:
                print(f"  警告: 没有解析出任何Markdown内容")

        finally:
            # 清理临时目录
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)

    def parse_local_pdfs_batch(self, pdf_paths: list, output_dir: str):
        """批量解析多个本地PDF"""
        for pdf_path in pdf_paths:
            base_name = Path(pdf_path).stem
            target_md = os.path.join(output_dir, f"{base_name}.md")
            if os.path.exists(target_md):
                print(f"跳过已解析: {base_name}")
                continue
            print(f"\n{'=' * 60}")
            print(f"开始解析: {Path(pdf_path).name}")
            print(f"{'=' * 60}")
            self.parse_local_pdf(pdf_path, output_dir)


if __name__ == "__main__":
    parser = MinerUParser()
    # 示例：解析单个PDF
    # parser.parse_local_pdf("test.pdf", "./output")
