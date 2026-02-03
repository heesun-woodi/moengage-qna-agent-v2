"""PDF History Importer - Import Q&A history from PDF files."""

import hashlib
import json
import io
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime
from dataclasses import dataclass, field

from anthropic import AsyncAnthropic

from config.settings import settings
from src.knowledge.history_rag import get_history_rag, HistoryEntry
from src.knowledge.history_updater import classify_category, CATEGORY_KEYWORDS
from src.utils.logger import logger

# Try to import pdfplumber
try:
    import pdfplumber
    PDFPLUMBER_AVAILABLE = True
except ImportError:
    PDFPLUMBER_AVAILABLE = False
    pdfplumber = None

# Try to import PIL for image handling
try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False
    Image = None


# Valid categories for classification
VALID_CATEGORIES = set(CATEGORY_KEYWORDS.keys())


@dataclass
class PDFParseResult:
    """Result of parsing a PDF Q&A entry."""
    title: str
    customer: str
    category: str
    query_summary: str
    solution: str
    slack_url: str = ""
    author: str = ""
    referenced_docs: List[str] = field(default_factory=list)
    image_analyses: List[str] = field(default_factory=list)
    raw_text: str = ""
    confidence: float = 0.0


# System prompt for PDF parsing
PDF_PARSER_SYSTEM_PROMPT = """당신은 PDF 문서에서 Q&A 이력을 추출하는 전문가입니다.

## 추출할 필드
PDF 텍스트에서 다음 필드를 찾아 추출하세요:
- 제목: 문서 상단의 큰 제목 또는 질문 요약
- 관련고객사: 고객사 이름 (레이블: "관련고객사", "고객사" 등)
- 슬랙 스레드 링크: Slack URL (레이블: "슬랙 스레드 링크", "MoEngage 슬랙 스레드 링크" 등)
- 작성자: 문서 작성자 (레이블: "작성자")
- 주제: 카테고리 (레이블: "주제")
- 문의 요약: 고객의 질문 요약 (레이블: "문의", "요약" 등)
- 최종 답변: 해결책 요약 (레이블: "최종 답변", "답변" 등)
- 관련 참고자료: URL 리스트 (레이블: "관련 참고자료", "참고자료" 등)

## 카테고리 목록 (8가지)
주제 필드를 다음 카테고리 중 하나로 매핑하세요:
1. 채널 세팅 및 연동 - 카카오, SMS, 이메일 등 채널 설정
2. 써드파티 연동 - 외부 서비스 연동
3. 데이터 모델 - 데이터, 속성, 이벤트 관련 (데이터 태깅 포함)
4. SDK 설치 - SDK 설치 및 초기화
5. Analyze 분석 기능 - 분석, 리포트, 대시보드
6. 유저 세그먼테이션 - 세그먼트, 타겟팅
7. 캠페인 세팅 - 캠페인, 푸시, 인앱 메시지
8. 기본 UI 가이드 - UI, 메뉴, 설정

## 출력 형식 (JSON만 출력)
```json
{
  "title": "문서 제목 또는 질문 요약",
  "customer": "고객사명",
  "slack_url": "https://...",
  "author": "작성자명",
  "category": "8가지 카테고리 중 하나",
  "query_summary": "문의 내용 요약",
  "solution": "최종 답변/해결책 요약",
  "referenced_docs": ["url1", "url2"],
  "confidence": 0.0-1.0
}
```

## 규칙
- 추출할 수 없는 필드는 빈 문자열("")로 설정
- confidence는 추출 품질에 따라 0.0~1.0 사이 값 설정
- 반드시 유효한 JSON만 출력하세요
"""


class PDFHistoryImporter:
    """Import Q&A history from PDF files into History RAG."""

    # Tracker file for deduplication
    TRACKER_FILENAME = "pdf_import_tracker.json"

    def __init__(self, pdf_dir: str = "Q&A history"):
        """Initialize PDF History Importer.

        Args:
            pdf_dir: Directory containing PDF files
        """
        if not PDFPLUMBER_AVAILABLE:
            raise RuntimeError("pdfplumber is not available. Install with: pip install pdfplumber")

        self.pdf_dir = Path(pdf_dir)
        self.tracker_path = Path(settings.chroma_persist_dir) / self.TRACKER_FILENAME
        self._tracker = self._load_tracker()
        self._current_pdf_path: Optional[Path] = None

    def _load_tracker(self) -> Dict[str, Any]:
        """Load import tracker from disk."""
        if self.tracker_path.exists():
            try:
                with open(self.tracker_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load tracker: {e}")
        return {"imported_files": {}}

    def _save_tracker(self):
        """Save import tracker to disk."""
        try:
            self.tracker_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.tracker_path, 'w', encoding='utf-8') as f:
                json.dump(self._tracker, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Failed to save tracker: {e}")

    def _file_hash(self, content: bytes) -> str:
        """Generate SHA256 hash of file content."""
        return hashlib.sha256(content).hexdigest()[:16]

    def _is_imported(self, filename: str, file_hash: str) -> bool:
        """Check if file was already imported."""
        entry = self._tracker["imported_files"].get(filename)
        if entry and entry.get("hash") == file_hash:
            return True
        return False

    def _mark_imported(self, filename: str, file_hash: str, entry_id: str):
        """Mark file as imported in tracker."""
        self._tracker["imported_files"][filename] = {
            "hash": file_hash,
            "imported_at": datetime.now().isoformat(),
            "entry_id": entry_id
        }
        self._save_tracker()

    def _extract_text_and_images(self, pdf_path: Path) -> Tuple[str, List[bytes]]:
        """Extract text and images from PDF using pdfplumber.

        Args:
            pdf_path: Path to PDF file

        Returns:
            Tuple of (extracted_text, list_of_image_bytes)
        """
        text_parts = []
        images = []

        with pdfplumber.open(pdf_path) as pdf:
            for page_num, page in enumerate(pdf.pages, 1):
                # Extract text
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(f"[Page {page_num}]\n{page_text}")

                # Extract images
                if PIL_AVAILABLE and page.images:
                    for img_info in page.images:
                        try:
                            # Get image bbox and extract
                            x0, y0, x1, y1 = img_info['x0'], img_info['top'], img_info['x1'], img_info['bottom']
                            # Crop page to image region
                            cropped = page.crop((x0, y0, x1, y1))
                            img = cropped.to_image(resolution=150)

                            # Convert to bytes
                            img_bytes = io.BytesIO()
                            img.original.save(img_bytes, format='PNG')
                            images.append(img_bytes.getvalue())
                        except Exception as e:
                            logger.debug(f"Failed to extract image from page {page_num}: {e}")

        return "\n\n".join(text_parts), images

    def _extract_text_and_images_from_bytes(self, pdf_bytes: bytes) -> Tuple[str, List[bytes]]:
        """Extract text and images from PDF bytes.

        Args:
            pdf_bytes: PDF file content as bytes

        Returns:
            Tuple of (extracted_text, list_of_image_bytes)
        """
        text_parts = []
        images = []

        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page_num, page in enumerate(pdf.pages, 1):
                # Extract text
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(f"[Page {page_num}]\n{page_text}")

                # Extract images
                if PIL_AVAILABLE and page.images:
                    for img_info in page.images:
                        try:
                            x0, y0, x1, y1 = img_info['x0'], img_info['top'], img_info['x1'], img_info['bottom']
                            cropped = page.crop((x0, y0, x1, y1))
                            img = cropped.to_image(resolution=150)

                            img_bytes_io = io.BytesIO()
                            img.original.save(img_bytes_io, format='PNG')
                            images.append(img_bytes_io.getvalue())
                        except Exception as e:
                            logger.debug(f"Failed to extract image from page {page_num}: {e}")

        return "\n\n".join(text_parts), images

    async def _analyze_images_with_vision(self, images: List[bytes]) -> List[str]:
        """Analyze images using Claude Vision API.

        Args:
            images: List of image bytes

        Returns:
            List of analysis strings
        """
        if not images:
            return []

        if not settings.anthropic_api_key:
            logger.warning("Anthropic API key not configured for image analysis")
            return []

        analyses = []
        client = AsyncAnthropic(api_key=settings.anthropic_api_key)

        # Limit to 5 images
        for i, img_bytes in enumerate(images[:5]):
            try:
                import base64
                base64_data = base64.standard_b64encode(img_bytes).decode('utf-8')

                response = await client.messages.create(
                    model="claude-sonnet-4-20250514",
                    max_tokens=500,
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "image",
                                    "source": {
                                        "type": "base64",
                                        "media_type": "image/png",
                                        "data": base64_data
                                    }
                                },
                                {
                                    "type": "text",
                                    "text": "이 이미지에서 보이는 내용을 간단히 설명해주세요. MoEngage 대시보드, 에러 메시지, 설정 화면 등이 있다면 중요한 정보를 추출해주세요. 한국어로 답변하세요."
                                }
                            ]
                        }
                    ]
                )

                analysis = response.content[0].text
                analyses.append(f"[이미지 {i+1}] {analysis}")
                logger.debug(f"Analyzed image {i+1}: {len(analysis)} chars")

            except Exception as e:
                logger.warning(f"Failed to analyze image {i+1}: {e}")

        return analyses

    async def _parse_with_claude(self, text: str, image_analyses: List[str]) -> PDFParseResult:
        """Parse PDF text using Claude to extract structured fields.

        Args:
            text: Extracted PDF text
            image_analyses: List of image analysis strings

        Returns:
            PDFParseResult with extracted fields
        """
        if not settings.anthropic_api_key:
            raise RuntimeError("Anthropic API key not configured")

        client = AsyncAnthropic(api_key=settings.anthropic_api_key)

        # Include image analyses in the prompt
        full_text = text
        if image_analyses:
            full_text += "\n\n## 이미지 분석 결과\n" + "\n".join(image_analyses)

        response = await client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1500,
            system=PDF_PARSER_SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": f"다음 PDF 텍스트에서 Q&A 정보를 추출해주세요:\n\n{full_text}"
                }
            ]
        )

        result_text = response.content[0].text

        # Parse JSON from response
        try:
            # Extract JSON from response
            if "```json" in result_text:
                json_str = result_text.split("```json")[1].split("```")[0]
            elif "```" in result_text:
                json_str = result_text.split("```")[1].split("```")[0]
            else:
                json_str = result_text

            data = json.loads(json_str.strip())

            # Map category to valid category
            category = data.get("category", "")
            if category not in VALID_CATEGORIES:
                # Try to classify from content
                category = classify_category(f"{data.get('query_summary', '')} {data.get('solution', '')}")

            return PDFParseResult(
                title=data.get("title", ""),
                customer=data.get("customer", ""),
                category=category,
                query_summary=data.get("query_summary", ""),
                solution=data.get("solution", ""),
                slack_url=data.get("slack_url", ""),
                author=data.get("author", ""),
                referenced_docs=data.get("referenced_docs", []),
                image_analyses=image_analyses,
                raw_text=text,
                confidence=data.get("confidence", 0.5)
            )

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse Claude response as JSON: {e}")
            logger.debug(f"Response was: {result_text}")

            # Fallback: extract basic info
            return PDFParseResult(
                title="PDF 파싱 실패",
                customer="",
                category="기타",
                query_summary=text[:500] if text else "",
                solution=result_text,
                image_analyses=image_analyses,
                raw_text=text,
                confidence=0.1
            )

    def _map_to_history_entry(self, result: PDFParseResult, filename: str) -> HistoryEntry:
        """Convert PDFParseResult to HistoryEntry.

        Args:
            result: Parsed PDF result
            filename: Original PDF filename

        Returns:
            HistoryEntry for RAG storage
        """
        # Combine solution with image analyses for richer context
        solution_parts = [result.solution]
        if result.image_analyses:
            solution_parts.append("\n\n### 스크린샷 분석\n" + "\n".join(result.image_analyses))

        return HistoryEntry(
            id="",  # Will be auto-generated
            title=result.title,
            customer=result.customer,
            category=result.category,
            query_summary=result.query_summary,
            solution="\n".join(solution_parts),
            created_at=datetime.now().isoformat(),
            url=result.slack_url,
            channel_id="",
            channel_name="",
            referenced_docs=result.referenced_docs,
            referenced_history=[],
            metadata={
                "source": "pdf_import",
                "author": result.author,
                "pdf_filename": filename,
                "import_confidence": result.confidence,
                "image_count": len(result.image_analyses)
            },
            source="pdf_import"
        )

    async def import_pdf(self, pdf_path: Path, force: bool = False) -> Optional[str]:
        """Import a single PDF file.

        Args:
            pdf_path: Path to PDF file
            force: Force re-import even if already imported

        Returns:
            Entry ID if successful, None otherwise
        """
        self._current_pdf_path = pdf_path

        if not pdf_path.exists():
            logger.error(f"PDF file not found: {pdf_path}")
            return None

        # Read file for hash
        with open(pdf_path, 'rb') as f:
            pdf_bytes = f.read()

        filename = pdf_path.name
        file_hash = self._file_hash(pdf_bytes)

        # Check if already imported
        if not force and self._is_imported(filename, file_hash):
            logger.info(f"PDF already imported: {filename}")
            return self._tracker["imported_files"][filename].get("entry_id")

        logger.info(f"Importing PDF: {filename}")

        try:
            # Extract text and images
            text, images = self._extract_text_and_images(pdf_path)

            if not text.strip():
                logger.warning(f"No text extracted from PDF: {filename}")
                return None

            logger.info(f"Extracted {len(text)} chars and {len(images)} images from {filename}")

            # Analyze images with Vision
            image_analyses = await self._analyze_images_with_vision(images)

            # Parse with Claude
            result = await self._parse_with_claude(text, image_analyses)

            logger.info(f"Parsed PDF - Title: {result.title}, Customer: {result.customer}, Category: {result.category}")

            # Map to HistoryEntry and add to RAG
            entry = self._map_to_history_entry(result, filename)
            rag = get_history_rag()
            entry_id = rag.add_entry(entry)

            # Mark as imported
            self._mark_imported(filename, file_hash, entry_id)

            logger.info(f"Successfully imported PDF: {filename} -> {entry_id}")
            return entry_id

        except Exception as e:
            logger.error(f"Failed to import PDF {filename}: {e}", exc_info=True)
            return None

    async def import_from_bytes(self, pdf_bytes: bytes, filename: str, force: bool = False) -> Optional[str]:
        """Import PDF from bytes (for Slack file attachments).

        Args:
            pdf_bytes: PDF file content as bytes
            filename: Original filename
            force: Force re-import even if already imported

        Returns:
            Entry ID if successful, None otherwise
        """
        file_hash = self._file_hash(pdf_bytes)

        # Check if already imported
        if not force and self._is_imported(filename, file_hash):
            logger.info(f"PDF already imported: {filename}")
            return self._tracker["imported_files"][filename].get("entry_id")

        logger.info(f"Importing PDF from bytes: {filename}")

        try:
            # Extract text and images
            text, images = self._extract_text_and_images_from_bytes(pdf_bytes)

            if not text.strip():
                logger.warning(f"No text extracted from PDF: {filename}")
                return None

            logger.info(f"Extracted {len(text)} chars and {len(images)} images from {filename}")

            # Analyze images with Vision
            image_analyses = await self._analyze_images_with_vision(images)

            # Parse with Claude
            result = await self._parse_with_claude(text, image_analyses)

            logger.info(f"Parsed PDF - Title: {result.title}, Customer: {result.customer}, Category: {result.category}")

            # Map to HistoryEntry and add to RAG
            entry = self._map_to_history_entry(result, filename)
            rag = get_history_rag()
            entry_id = rag.add_entry(entry)

            # Mark as imported
            self._mark_imported(filename, file_hash, entry_id)

            logger.info(f"Successfully imported PDF: {filename} -> {entry_id}")
            return entry_id

        except Exception as e:
            logger.error(f"Failed to import PDF {filename}: {e}", exc_info=True)
            return None

    async def import_all(self, force: bool = False) -> Dict[str, Any]:
        """Import all PDF files from the directory.

        Args:
            force: Force re-import all files

        Returns:
            Summary of import results
        """
        if not self.pdf_dir.exists():
            logger.warning(f"PDF directory does not exist: {self.pdf_dir}")
            return {"success": 0, "skipped": 0, "failed": 0, "errors": []}

        pdf_files = list(self.pdf_dir.glob("*.pdf"))
        logger.info(f"Found {len(pdf_files)} PDF files in {self.pdf_dir}")

        results = {
            "success": 0,
            "skipped": 0,
            "failed": 0,
            "errors": [],
            "imported_entries": []
        }

        for pdf_path in pdf_files:
            try:
                entry_id = await self.import_pdf(pdf_path, force=force)
                if entry_id:
                    results["success"] += 1
                    results["imported_entries"].append({
                        "filename": pdf_path.name,
                        "entry_id": entry_id
                    })
                else:
                    # Check if skipped due to already imported
                    filename = pdf_path.name
                    if filename in self._tracker["imported_files"] and not force:
                        results["skipped"] += 1
                    else:
                        results["failed"] += 1
            except Exception as e:
                results["failed"] += 1
                results["errors"].append({
                    "filename": pdf_path.name,
                    "error": str(e)
                })
                logger.error(f"Error importing {pdf_path.name}: {e}")

        logger.info(
            f"Import complete: {results['success']} success, "
            f"{results['skipped']} skipped, {results['failed']} failed"
        )

        return results

    async def import_pdf_remote(self, pdf_path: Path, force: bool = False) -> Optional[str]:
        """Import a single PDF file to remote Railway API.

        Args:
            pdf_path: Path to PDF file
            force: Force re-import even if already imported

        Returns:
            Entry ID if successful, None otherwise
        """
        from src.knowledge.history_api_client import HistoryAPIClient

        self._current_pdf_path = pdf_path

        if not pdf_path.exists():
            logger.error(f"PDF file not found: {pdf_path}")
            return None

        # Read file for hash
        with open(pdf_path, 'rb') as f:
            pdf_bytes = f.read()

        filename = pdf_path.name
        file_hash = self._file_hash(pdf_bytes)

        # Check if already imported (local tracker)
        if not force and self._is_imported(filename, file_hash):
            logger.info(f"PDF already imported: {filename}")
            return self._tracker["imported_files"][filename].get("entry_id")

        logger.info(f"Importing PDF to remote: {filename}")

        try:
            # Extract text and images
            text, images = self._extract_text_and_images(pdf_path)

            if not text.strip():
                logger.warning(f"No text extracted from PDF: {filename}")
                return None

            logger.info(f"Extracted {len(text)} chars and {len(images)} images from {filename}")

            # Analyze images with Vision
            image_analyses = await self._analyze_images_with_vision(images)

            # Parse with Claude
            result = await self._parse_with_claude(text, image_analyses)

            logger.info(f"Parsed PDF - Title: {result.title}, Customer: {result.customer}, Category: {result.category}")

            # Map to HistoryEntry
            entry = self._map_to_history_entry(result, filename)

            # Send to remote API instead of local RAG
            client = HistoryAPIClient()
            entry_id = await client.add_entry(entry)

            if entry_id:
                # Mark as imported in local tracker
                self._mark_imported(filename, file_hash, entry_id)
                logger.info(f"Successfully imported PDF to remote: {filename} -> {entry_id}")
                return entry_id
            else:
                logger.error(f"Failed to import PDF to remote: {filename}")
                return None

        except Exception as e:
            logger.error(f"Failed to import PDF {filename}: {e}", exc_info=True)
            return None

    async def import_all_remote(self, force: bool = False) -> Dict[str, Any]:
        """Import all PDF files from the directory to remote Railway API.

        Args:
            force: Force re-import all files

        Returns:
            Summary of import results
        """
        if not self.pdf_dir.exists():
            logger.warning(f"PDF directory does not exist: {self.pdf_dir}")
            return {"success": 0, "skipped": 0, "failed": 0, "errors": []}

        pdf_files = list(self.pdf_dir.glob("*.pdf"))
        logger.info(f"Found {len(pdf_files)} PDF files in {self.pdf_dir}")

        results = {
            "success": 0,
            "skipped": 0,
            "failed": 0,
            "errors": [],
            "imported_entries": []
        }

        for pdf_path in pdf_files:
            try:
                entry_id = await self.import_pdf_remote(pdf_path, force=force)
                if entry_id:
                    results["success"] += 1
                    results["imported_entries"].append({
                        "filename": pdf_path.name,
                        "entry_id": entry_id
                    })
                else:
                    # Check if skipped due to already imported
                    filename = pdf_path.name
                    if filename in self._tracker["imported_files"] and not force:
                        results["skipped"] += 1
                    else:
                        results["failed"] += 1
            except Exception as e:
                results["failed"] += 1
                results["errors"].append({
                    "filename": pdf_path.name,
                    "error": str(e)
                })
                logger.error(f"Error importing {pdf_path.name}: {e}")

        logger.info(
            f"Remote import complete: {results['success']} success, "
            f"{results['skipped']} skipped, {results['failed']} failed"
        )

        return results


async def download_slack_file(client, url: str, token: str) -> Optional[bytes]:
    """Download file from Slack.

    Args:
        client: Slack WebClient (not used, kept for compatibility)
        url: File URL
        token: Slack bot token

    Returns:
        File content as bytes or None if failed
    """
    import aiohttp

    try:
        headers = {
            'Authorization': f'Bearer {token}',
            'User-Agent': 'MoEngage-QA-Bot/1.0'
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=30) as response:
                if response.status != 200:
                    logger.error(f"Failed to download Slack file: HTTP {response.status}")
                    return None
                return await response.read()

    except Exception as e:
        logger.error(f"Error downloading Slack file: {e}")
        return None


def get_pdf_importer(pdf_dir: str = "Q&A history") -> PDFHistoryImporter:
    """Get PDFHistoryImporter instance.

    Args:
        pdf_dir: Directory containing PDF files

    Returns:
        PDFHistoryImporter instance
    """
    return PDFHistoryImporter(pdf_dir)
