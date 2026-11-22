"""Work areas matched against original RFQ clause text.

A company department ticks the work areas it receives; a clause belongs to every
area whose keywords appear in its text. Ten areas, each grouping related topics
(the first version had 34, too many to assign by hand).
"""

import re

# Catalog order is also the tie breaker for equally specific matches.
# Each area: (label, the narrower topics it groups -> their keywords).
GROUPS: dict[str, tuple[str, dict[str, str]]] = {
    "bidding": (
        "입찰 일정·절차·협상",
        {
            "intake": (
                "rfq schedule,submission deadline,quotation deadline,quote due,angebotsfrist,일정,접수"
            ),
            "tender": (
                "bidding procedure,tender procedure,appeal,vergabeverfahren,einspruch,입찰 절차,이의제기"
            ),
            "negotiation": (
                "negotiation,deviation request,exception request,verhandlung,abweichungsantrag,협상,예외 요청"
            ),
        },
    ),
    "pricing": (
        "견적가·원가·가격 조건",
        {
            "quote": ("target price,quotation price,quoted price,zielpreis,angebotspreis,목표가,견적가"),
            "breakdown": (
                "cost breakdown,cost structure,kostenaufschlüsselung,kostenstruktur,원가 분해,원가분해"
            ),
            "price-lock": (
                "price reduction,fixed price,price freeze,festpreis,preissenkung,단가 인하,가격 고정"
            ),
            "investment": "tooling cost,investment,tool ownership,werkzeugkosten,eigentum,투자비,소유권",
        },
    ),
    "payment": (
        "결제·세금·통화",
        {
            "payment": "payment,invoicing,invoice,zahlung,rechnung,결제,청구",
            "currency": "currency,tax,vat,währung,steuer,세금,통화",
        },
    ),
    "volume": (
        "물량·생산능력",
        {
            "volume": "volume,quantity,forecast,stückzahl,menge,물량,수량",
            "capacity": "capacity,flexibility,kapazität,flexibilität,생산능력,유연성",
        },
    ),
    "engineering": (
        "도면·사양·시험 검증",
        {
            "specification": (
                "drawing,specification,engineering change,zeichnung,spezifikation,도면,사양,설계변경"
            ),
            "validation": "testing,test plan,validation,prüfung,validierung,시험,검증",
        },
    ),
    "quality-assurance": (
        "품질·인증·양산 승인·클레임",
        {
            "credentials": (
                "qualification evidence,proof of certification,qualification documents,"
                "nachweis,eignung,증빙,자격"
            ),
            "quality-system": "iatf,iso 9001,quality system,qualitätsmanagement,품질 시스템,품질 인증",
            "approval": "ppap,production part approval,serienfreigabe,양산 승인",
            "capability": "cpk,ppk,ppm,process capability,prozessfähigkeit,공정능력,불량 목표",
            "traceability": (
                "special process,cleanliness,traceability,sauberkeit,rückverfolgbarkeit,"
                "특수공정,청정도,추적성"
            ),
            "claims": "claim,warranty claim,reklamation,gewährleistungsabwicklung,클레임,보증 이행",
        },
    ),
    "production": (
        "생산 공정·소재·협력사",
        {
            "production": (
                "production process,equipment,manufacturing process,fertigungsprozess,anlage,생산 공정,설비"
            ),
            "material": "raw material,material sourcing,steel grade,rohstoff,werkstoff,소재,원자재",
            "subcontract": (
                "subcontract,subcontractor,subsupplier,unterlieferant,unterauftragnehmer,외주,협력사"
            ),
            "parts": (
                "component sourcing,alternative part,substitute part,ersatzteil,"
                "alternativteil,부품 조달,대체품"
            ),
        },
    ),
    "delivery": (
        "물류·납품·포장·재고",
        {
            "freight": ("incoterms,incoterm,freight,delivery terms,fracht,lieferbedingung,인도 조건,운임"),
            "delivery": (
                "delivery schedule,lead time,delivery date,just in time,liefertermin,"
                "lieferzeit,납품 방식,납기"
            ),
            "packaging": "packaging,label,labelling,verpackung,etikett,포장,라벨",
            "inventory": "inventory,safety stock,stock level,sicherheitsbestand,lagerbestand,재고",
            "shipping-docs": (
                "delivery note,shipping document,dispatch advice,lieferschein,"
                "versandanzeige,납품 서류,납품 보고"
            ),
        },
    ),
    "contract": (
        "계약·책임·지재권·법규",
        {
            "contract": (
                "contract terms,general terms,governing law,vertragsbedingungen,"
                "gerichtsstand,계약 조건,준거법"
            ),
            "liability": ("warranty,liability,insurance,gewährleistung,haftung,versicherung,보증,책임,보험"),
            "ip": (
                "intellectual property,confidential,disclosure,patent,geheimhaltung,"
                "schutzrechte,지재권,비밀유지,정보공개"
            ),
            "compliance": "regulatory,compliance,reach,rohs,gesetzliche,konformität,법규,규제",
        },
    ),
    "it-site": (
        "정보보안·현장·환경",
        {
            "integration": (
                "edi,interface,cybersecurity,information security,schnittstelle,"
                "informationssicherheit,시스템 연동,정보보안"
            ),
            "site": ("environmental,personnel,site safety,umwelt,personal,arbeitsschutz,환경,인력,현장"),
        },
    ),
}

CATALOG: dict[str, tuple[str, str]] = {
    key: (label, ",".join(topics.values())) for key, (label, topics) in GROUPS.items()
}
"""Area id -> (label, comma-separated keywords)."""

LEGACY: dict[str, str] = {topic: key for key, (_, topics) in GROUPS.items() for topic in topics}
"""The 34 earlier area ids -> the area that now holds them, so saved departments keep working."""


def canonical_area(area: str) -> str:
    """The current id for an area id (an earlier fine-grained id maps to its group)."""
    return LEGACY.get(area, area)


def _pattern(term: str) -> re.Pattern:
    # Korean words take suffixes; short Latin acronyms must match whole tokens.
    if term.isascii() and len(term) <= 4:
        return re.compile(r"\b" + re.escape(term) + r"\b")
    return re.compile(re.escape(term))


_PATTERNS = {key: tuple(_pattern(term) for term in terms.split(",")) for key, (_, terms) in CATALOG.items()}


def matching_areas(text: str) -> list[str]:
    normalized = " ".join(text.lower().split())
    scores = {
        key: sum(len(pattern.pattern) for pattern in patterns if pattern.search(normalized))
        for key, patterns in _PATTERNS.items()
    }
    return sorted((key for key in scores if scores[key]), key=scores.__getitem__, reverse=True)
