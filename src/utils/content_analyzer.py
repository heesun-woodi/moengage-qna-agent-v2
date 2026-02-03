"""Content analyzer - Extract and analyze URLs and images from messages."""

import re
import base64
import asyncio
import aiohttp
from typing import List, Dict, Any, Optional, Tuple
from bs4 import BeautifulSoup

from anthropic import AsyncAnthropic

from config.settings import settings
from src.utils.logger import logger


# URL pattern for extraction
URL_PATTERN = re.compile(
    r'https?://[^\s<>\[\]()"\'\|]+',
    re.IGNORECASE
)

# Image extensions
IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp'}


def extract_urls_from_text(text: str) -> List[str]:
    """Extract URLs from text.

    Args:
        text: Text to extract URLs from

    Returns:
        List of extracted URLs
    """
    if not text:
        return []

    urls = URL_PATTERN.findall(text)
    # Clean up URLs (remove trailing punctuation)
    cleaned = []
    for url in urls:
        url = url.rstrip('.,;:!?)')
        if url:
            cleaned.append(url)

    return list(set(cleaned))  # Remove duplicates


def extract_image_urls_from_attachments(attachments: List[Dict[str, Any]]) -> List[str]:
    """Extract image URLs from Slack attachments.

    Args:
        attachments: List of Slack attachment dictionaries

    Returns:
        List of image URLs
    """
    image_urls = []

    for attachment in attachments:
        # Check for image_url field
        if 'image_url' in attachment:
            image_urls.append(attachment['image_url'])

        # Check for thumb_url as fallback
        if 'thumb_url' in attachment:
            image_urls.append(attachment['thumb_url'])

        # Check files array (for file uploads)
        if 'files' in attachment:
            for file in attachment['files']:
                if file.get('mimetype', '').startswith('image/'):
                    # Prefer url_private for higher quality
                    url = file.get('url_private') or file.get('url_private_download')
                    if url:
                        image_urls.append(url)

    return list(set(image_urls))


def extract_images_from_slack_files(files: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Extract image information from Slack file objects.

    Args:
        files: List of Slack file dictionaries

    Returns:
        List of dicts with 'url' and 'name' keys
    """
    images = []

    for file in files:
        mimetype = file.get('mimetype', '')
        if mimetype.startswith('image/'):
            url = file.get('url_private') or file.get('url_private_download')
            if url:
                images.append({
                    'url': url,
                    'name': file.get('name', 'image'),
                    'mimetype': mimetype
                })

    return images


async def fetch_url_content(
    url: str,
    timeout: int = 10,
    max_length: int = 5000
) -> Optional[str]:
    """Fetch and extract text content from a URL.

    Args:
        url: URL to fetch
        timeout: Request timeout in seconds
        max_length: Maximum content length to return

    Returns:
        Extracted text content or None if failed
    """
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=timeout),
                headers={'User-Agent': 'MoEngage-QA-Bot/1.0'}
            ) as response:
                if response.status != 200:
                    logger.warning(f"Failed to fetch URL {url}: HTTP {response.status}")
                    return None

                content_type = response.headers.get('Content-Type', '')

                # Only process HTML content
                if 'text/html' not in content_type:
                    logger.debug(f"Skipping non-HTML URL: {url}")
                    return None

                html = await response.text()

        # Parse HTML and extract text
        soup = BeautifulSoup(html, 'html.parser')

        # Remove script and style elements
        for element in soup(['script', 'style', 'nav', 'footer', 'header']):
            element.decompose()

        # Get text content
        text = soup.get_text(separator=' ', strip=True)

        # Get title
        title = soup.find('title')
        title_text = title.get_text(strip=True) if title else ''

        # Combine title and content
        result = f"Title: {title_text}\n\n{text}" if title_text else text

        # Truncate if too long
        if len(result) > max_length:
            result = result[:max_length] + "..."

        logger.debug(f"Fetched URL content: {url} ({len(result)} chars)")
        return result

    except asyncio.TimeoutError:
        logger.warning(f"Timeout fetching URL: {url}")
        return None
    except Exception as e:
        logger.warning(f"Error fetching URL {url}: {e}")
        return None


async def fetch_image_as_base64(
    url: str,
    slack_token: str = None,
    timeout: int = 15
) -> Optional[Tuple[str, str]]:
    """Fetch image and convert to base64.

    Args:
        url: Image URL to fetch
        slack_token: Slack bot token for private URLs
        timeout: Request timeout in seconds

    Returns:
        Tuple of (base64_data, media_type) or None if failed
    """
    try:
        headers = {'User-Agent': 'MoEngage-QA-Bot/1.0'}

        # Add Slack authorization for private URLs
        if slack_token and 'slack' in url.lower():
            headers['Authorization'] = f'Bearer {slack_token}'

        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=timeout),
                headers=headers
            ) as response:
                if response.status != 200:
                    logger.warning(f"Failed to fetch image {url}: HTTP {response.status}")
                    return None

                content_type = response.headers.get('Content-Type', 'image/png')
                image_data = await response.read()

                # Convert to base64
                base64_data = base64.standard_b64encode(image_data).decode('utf-8')

                # Determine media type
                if 'jpeg' in content_type or 'jpg' in content_type:
                    media_type = 'image/jpeg'
                elif 'png' in content_type:
                    media_type = 'image/png'
                elif 'gif' in content_type:
                    media_type = 'image/gif'
                elif 'webp' in content_type:
                    media_type = 'image/webp'
                else:
                    media_type = 'image/png'  # Default

                logger.debug(f"Fetched image: {url} ({len(image_data)} bytes)")
                return base64_data, media_type

    except asyncio.TimeoutError:
        logger.warning(f"Timeout fetching image: {url}")
        return None
    except Exception as e:
        logger.warning(f"Error fetching image {url}: {e}")
        return None


async def analyze_image_with_vision(
    image_url: str,
    slack_token: str = None,
    prompt: str = "이 이미지에서 보이는 내용을 간단히 설명해주세요. 특히 MoEngage 대시보드, 에러 메시지, 설정 화면 등이 있다면 중요한 정보를 추출해주세요."
) -> Optional[str]:
    """Analyze an image using Claude Vision API.

    Args:
        image_url: URL of the image to analyze
        slack_token: Slack bot token for private URLs
        prompt: Prompt for image analysis

    Returns:
        Analysis result or None if failed
    """
    if not settings.anthropic_api_key:
        logger.warning("Anthropic API key not configured for image analysis")
        return None

    # Fetch image as base64
    result = await fetch_image_as_base64(image_url, slack_token)
    if not result:
        return None

    base64_data, media_type = result

    try:
        client = AsyncAnthropic(api_key=settings.anthropic_api_key)

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
                                "media_type": media_type,
                                "data": base64_data
                            }
                        },
                        {
                            "type": "text",
                            "text": prompt
                        }
                    ]
                }
            ]
        )

        analysis = response.content[0].text
        logger.info(f"Analyzed image: {image_url[:50]}... ({len(analysis)} chars)")
        return analysis

    except Exception as e:
        logger.error(f"Error analyzing image with Vision API: {e}")
        return None


async def analyze_message_content(
    text: str,
    attachments: List[Dict[str, Any]] = None,
    files: List[Dict[str, Any]] = None,
    slack_token: str = None
) -> Dict[str, Any]:
    """Analyze message content including URLs and images.

    Args:
        text: Message text
        attachments: Slack attachments
        files: Slack files
        slack_token: Slack bot token for private URLs

    Returns:
        Dictionary with analyzed content:
        - urls: List of extracted URLs
        - url_contents: Dict mapping URL to extracted content
        - images: List of image URLs
        - image_analyses: Dict mapping image URL to analysis
        - combined_context: Combined text from all sources
    """
    if attachments is None:
        attachments = []
    if files is None:
        files = []

    result = {
        'urls': [],
        'url_contents': {},
        'images': [],
        'image_analyses': {},
        'combined_context': text
    }

    # Extract URLs from text
    urls = extract_urls_from_text(text)
    result['urls'] = urls

    # Extract images from attachments and files
    image_urls = extract_image_urls_from_attachments(attachments)
    for file_info in extract_images_from_slack_files(files):
        image_urls.append(file_info['url'])
    result['images'] = list(set(image_urls))

    # Fetch URL contents in parallel
    url_tasks = []
    for url in urls[:5]:  # Limit to 5 URLs
        url_tasks.append(fetch_url_content(url))

    if url_tasks:
        url_results = await asyncio.gather(*url_tasks, return_exceptions=True)
        for url, content in zip(urls[:5], url_results):
            if isinstance(content, str) and content:
                result['url_contents'][url] = content

    # Analyze images in parallel
    image_tasks = []
    for img_url in result['images'][:3]:  # Limit to 3 images
        image_tasks.append(analyze_image_with_vision(img_url, slack_token))

    if image_tasks:
        image_results = await asyncio.gather(*image_tasks, return_exceptions=True)
        for img_url, analysis in zip(result['images'][:3], image_results):
            if isinstance(analysis, str) and analysis:
                result['image_analyses'][img_url] = analysis

    # Build combined context
    context_parts = [text]

    for url, content in result['url_contents'].items():
        context_parts.append(f"\n[URL 내용: {url}]\n{content[:1000]}")

    for img_url, analysis in result['image_analyses'].items():
        context_parts.append(f"\n[이미지 분석]\n{analysis}")

    result['combined_context'] = "\n".join(context_parts)

    return result


async def extract_search_keywords(
    analyzed_content: Dict[str, Any],
    original_query: str
) -> Dict[str, Any]:
    """Extract optimized search keywords from analyzed content.

    Args:
        analyzed_content: Result from analyze_message_content
        original_query: Original user query

    Returns:
        Dictionary with:
        - korean_keywords: List of Korean keywords
        - english_keywords: List of English keywords
        - search_query: Optimized search query
    """
    from src.utils.term_mapper import map_korean_to_english

    combined_text = analyzed_content.get('combined_context', original_query)

    # Extract Korean keywords (simple approach)
    korean_keywords = []

    # MoEngage related terms to look for
    moengage_terms = [
        '세그먼트', '캠페인', '푸시', '인앱', '이메일', 'SMS',
        '대시보드', '분석', '이벤트', '속성', '유저', '사용자',
        '플로우', '카카오', '연동', '설정', 'SDK', '앱'
    ]

    for term in moengage_terms:
        if term.lower() in combined_text.lower():
            korean_keywords.append(term)

    # Map to English keywords
    english_keywords = []
    for kw in korean_keywords:
        mapped = map_korean_to_english(kw)
        english_keywords.extend(mapped.split())

    # Remove duplicates
    english_keywords = list(set(english_keywords))

    # Build optimized search query
    search_parts = english_keywords[:5]  # Top 5 keywords
    search_query = ' '.join(search_parts) if search_parts else original_query

    return {
        'korean_keywords': korean_keywords,
        'english_keywords': english_keywords,
        'search_query': search_query
    }
