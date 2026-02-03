"""Korean-English term mapping for MoEngage terminology."""

from typing import List, Dict


# MoEngage terminology mapping (Korean -> English)
TERM_MAPPING: Dict[str, List[str]] = {
    # Core concepts
    "세그먼트": ["Segment", "Segmentation"],
    "세그먼테이션": ["Segmentation", "Segment"],
    "캠페인": ["Campaign", "Campaigns"],
    "플로우": ["Flow", "Flows", "Journey"],
    "여정": ["Journey", "Flow", "Flows"],

    # Channels
    "푸시": ["Push", "Push Notification", "Push Notifications"],
    "푸시 알림": ["Push Notification", "Push Notifications"],
    "인앱": ["In-App", "In-App Message", "In-App NATIV"],
    "이메일": ["Email", "Email Campaign"],
    "카카오": ["Kakao", "KakaoTalk"],
    "SMS": ["SMS", "Text Message"],
    "웹푸시": ["Web Push", "Web Push Notification"],

    # Data & User Properties
    "속성": ["Attribute", "User Attribute", "Property"],
    "사용자 속성": ["User Attribute", "User Property"],
    "유저속성": ["User Attribute", "User Property"],
    "유저 속성": ["User Attribute", "User Property"],
    "유저프로퍼티": ["User Property", "User Attribute", "Custom Attribute"],
    "유저 프로퍼티": ["User Property", "User Attribute", "Custom Attribute"],
    "커스텀속성": ["Custom Attribute", "User Attribute"],
    "커스텀 속성": ["Custom Attribute", "User Attribute"],
    "이벤트": ["Event", "Events", "User Event"],
    "데이터": ["Data", "User Data"],
    "이벤트속성": ["Event Attribute", "Event Property"],
    "이벤트 속성": ["Event Attribute", "Event Property"],

    # Data Import/Export
    "업로드": ["Upload", "Import", "CSV Upload", "Data Import"],
    "임포트": ["Import", "Data Import", "CSV Import"],
    "CSV": ["CSV", "CSV Upload", "CSV Import"],
    "엑셀": ["Excel", "CSV", "Spreadsheet"],
    "다운로드": ["Download", "Export"],
    "익스포트": ["Export", "Data Export"],
    "내보내기": ["Export", "Data Export"],
    "가져오기": ["Import", "Data Import"],

    # Analytics
    "분석": ["Analytics", "Analysis", "Analyze"],
    "대시보드": ["Dashboard"],
    "리포트": ["Report", "Reports"],
    "퍼널": ["Funnel", "Funnel Analysis"],
    "리텐션": ["Retention", "User Retention"],
    "코호트": ["Cohort", "Cohort Analysis"],
    "집계": ["Aggregation", "Count", "Analytics"],
    "통계": ["Statistics", "Analytics", "Metrics"],

    # Features
    "머신러닝": ["Machine Learning", "Merlin AI", "AI"],
    "멀린": ["Merlin", "Merlin AI"],
    "최적화": ["Optimization", "Optimize"],
    "A/B 테스트": ["A/B Test", "A/B Testing", "Split Test"],
    "개인화": ["Personalization", "Personalize"],

    # Settings & Integration
    "설정": ["Settings", "Configuration"],
    "연동": ["Integration", "Connect", "Connector"],
    "SDK": ["SDK", "Software Development Kit"],
    "API": ["API", "Application Programming Interface"],
    "토큰": ["Token", "API Token", "FCM Token"],
    "인증": ["Authentication", "Auth", "API Key"],

    # User Management
    "유저": ["User", "Users"],
    "사용자": ["User", "Users"],
    "신규 가입자": ["New User", "New Registration", "User Onboarding"],
    "신규가입자": ["New User", "New Registration", "User Onboarding"],
    "신규 유저": ["New User", "New Registration"],
    "가입자": ["Subscriber", "User", "Registered User"],

    # Issues & Troubleshooting
    "오류": ["Error", "Issue", "Problem"],
    "실패": ["Failure", "Failed", "Error"],
    "발송": ["Send", "Delivery", "Dispatch"],
    "발송 실패": ["Delivery Failure", "Send Failed", "Delivery Error"],
    "누락": ["Missing", "Not Delivered", "Dropped"],
    "수집": ["Collection", "Data Collection", "Tracking"],
    "수집 안됨": ["Not Collected", "Missing Data", "Data Loss"],
    "지연": ["Delay", "Latency", "Delayed"],
    "중복": ["Duplicate", "Duplicated"],

    # Actions
    "수동": ["Manual", "Manually"],
    "자동": ["Automatic", "Auto", "Automated"],
    "생성": ["Create", "Creation"],
    "삭제": ["Delete", "Remove"],
    "수정": ["Edit", "Modify", "Update"],
    "조회": ["View", "Query", "Search"],
}


def expand_korean_query(query: str) -> str:
    """Expand Korean query with English terms for better search.

    Args:
        query: Korean search query

    Returns:
        Expanded query with English terms appended
    """
    expanded_terms: List[str] = [query]

    for korean_term, english_terms in TERM_MAPPING.items():
        if korean_term in query:
            # Add first (primary) English term
            expanded_terms.append(english_terms[0])

    return " ".join(expanded_terms)


def get_english_keywords(korean_query: str) -> List[str]:
    """Extract English keywords from Korean query.

    Args:
        korean_query: Korean search query

    Returns:
        List of English keywords
    """
    keywords: List[str] = []

    for korean_term, english_terms in TERM_MAPPING.items():
        if korean_term in korean_query:
            keywords.extend(english_terms[:2])  # Add top 2 English terms

    return list(set(keywords))  # Remove duplicates


def translate_for_search(korean_query: str) -> str:
    """Create optimized English search query from Korean.

    Args:
        korean_query: Korean search query

    Returns:
        English search query optimized for MoEngage docs
    """
    english_keywords = get_english_keywords(korean_query)

    if english_keywords:
        return " ".join(english_keywords)

    # If no mapping found, return original (API will handle it)
    return korean_query
