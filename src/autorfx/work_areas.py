"""Work areas matched against original RFQ clause text."""

import re

# Catalog order is also the tie breaker for equally specific matches.
CATALOG = {
    "intake": (
        "RFQ 접수·일정 관리",
        "rfq schedule,submission deadline,quotation deadline,quote due,angebotsfrist,일정,접수",
    ),
    "negotiation": (
        "고객 협상·예외 요청",
        "negotiation,deviation request,exception request,verhandlung,abweichungsantrag,협상,예외 요청",
    ),
    "volume": ("수량·물량 리스크", "volume,quantity,forecast,stückzahl,menge,물량,수량"),
    "credentials": (
        "자격·인증 증빙 취합",
        "qualification evidence,proof of certification,qualification documents,nachweis,eignung,증빙,자격",
    ),
    "quote": (
        "목표가·견적가 산정",
        "target price,quotation price,quoted price,zielpreis,angebotspreis,목표가,견적가",
    ),
    "breakdown": (
        "원가 분해",
        "cost breakdown,cost structure,kostenaufschlüsselung,kostenstruktur,원가 분해,원가분해",
    ),
    "price-lock": (
        "연간 인하·가격 고정",
        "price reduction,fixed price,price freeze,festpreis,preissenkung,단가 인하,가격 고정",
    ),
    "investment": (
        "투자비·자산 소유권",
        "tooling cost,investment,tool ownership,werkzeugkosten,eigentum,투자비,소유권",
    ),
    "payment": ("결제·청구 조건", "payment,invoicing,invoice,zahlung,rechnung,결제,청구"),
    "currency": ("세금·통화", "currency,tax,vat,währung,steuer,세금,통화"),
    "specification": (
        "도면·사양 검토·변경 관리",
        "drawing,specification,engineering change,zeichnung,spezifikation,도면,사양,설계변경",
    ),
    "validation": ("시험·검증 요건", "testing,test plan,validation,prüfung,validierung,시험,검증"),
    "integration": (
        "시스템 연동·정보보안",
        "edi,interface,cybersecurity,information security,schnittstelle,"
        "informationssicherheit,시스템 연동,정보보안",
    ),
    "quality-system": (
        "품질 시스템·인증",
        "iatf,iso 9001,quality system,qualitätsmanagement,품질 시스템,품질 인증",
    ),
    "approval": ("양산 승인", "ppap,production part approval,serienfreigabe,양산 승인"),
    "capability": (
        "공정능력·불량 목표",
        "cpk,ppk,ppm,process capability,prozessfähigkeit,공정능력,불량 목표",
    ),
    "traceability": (
        "특수공정·청정도·추적성",
        "special process,cleanliness,traceability,sauberkeit,rückverfolgbarkeit,특수공정,청정도,추적성",
    ),
    "claims": (
        "클레임·보증 이행",
        "claim,warranty claim,reklamation,gewährleistungsabwicklung,클레임,보증 이행",
    ),
    "capacity": ("생산능력·유연성", "capacity,flexibility,kapazität,flexibilität,생산능력,유연성"),
    "production": (
        "생산 공정·설비 관리",
        "production process,equipment,manufacturing process,fertigungsprozess,anlage,생산 공정,설비",
    ),
    "material": ("소재 조달", "raw material,material sourcing,steel grade,rohstoff,werkstoff,소재,원자재"),
    "subcontract": (
        "외주·협력사 관리",
        "subcontract,subcontractor,subsupplier,unterlieferant,unterauftragnehmer,외주,협력사",
    ),
    "parts": (
        "부품 조달·대체품 승인",
        "component sourcing,alternative part,substitute part,ersatzteil,alternativteil,부품 조달,대체품",
    ),
    "freight": (
        "인도 조건·운임",
        "incoterms,incoterm,freight,delivery terms,fracht,lieferbedingung,인도 조건,운임",
    ),
    "delivery": (
        "납품 방식·납기",
        "delivery schedule,lead time,delivery date,just in time,liefertermin,lieferzeit,납품 방식,납기",
    ),
    "packaging": ("포장·라벨", "packaging,label,labelling,verpackung,etikett,포장,라벨"),
    "inventory": ("재고·안전재고", "inventory,safety stock,stock level,sicherheitsbestand,lagerbestand,재고"),
    "shipping-docs": (
        "납품 서류·보고",
        "delivery note,shipping document,dispatch advice,lieferschein,versandanzeige,납품 서류,납품 보고",
    ),
    "contract": (
        "일반 계약 조건",
        "contract terms,general terms,governing law,vertragsbedingungen,gerichtsstand,계약 조건,준거법",
    ),
    "liability": (
        "보증·책임·보험",
        "warranty,liability,insurance,gewährleistung,haftung,versicherung,보증,책임,보험",
    ),
    "ip": (
        "지재권·비밀유지·정보공개",
        "intellectual property,confidential,disclosure,patent,geheimhaltung,"
        "schutzrechte,지재권,비밀유지,정보공개",
    ),
    "compliance": ("법규·규제 준수", "regulatory,compliance,reach,rohs,gesetzliche,konformität,법규,규제"),
    "tender": (
        "입찰 절차·이의제기",
        "bidding procedure,tender procedure,appeal,vergabeverfahren,einspruch,입찰 절차,이의제기",
    ),
    "site": (
        "환경·인력·현장 요건",
        "environmental,personnel,site safety,umwelt,personal,arbeitsschutz,환경,인력,현장",
    ),
}


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
