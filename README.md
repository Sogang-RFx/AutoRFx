# AutoRFx

> 자동차 부품 협력사(Tier-1/Tier-2)를 위한 다국어 RFQ 분석 · 정밀 Citation 추적 · 사전원가 판단 Agentic RAG 플랫폼  
> **서강대학교 빅데이터종합설계(캡스톤디자인) 3조 × (주)겁쟁이사자들**

---

## 핵심 설계 원칙

1. **원본 불변 원칙 (Immutable Original)**: 계약 효력을 지닌 RFQ 원문의 텍스트·표·서식을 임의로 변형하지 않고, 원본 문서의 페이지/문장 위치(Locus) 메타데이터를 끝까지 보존합니다.
2. **정밀 Citation Enforcer**: 부서별 요약이나 독소조항 탐지 결과를 클릭하면 원본 문서의 정확한 위치(예: `30페이지 3번째 문장`)를 역추적하여 할루시네이션을 차단합니다.
3. **번역·판별 하이브리드 분업**: 다국어 번역은 전문 번역 레이어로 처리하고, LLM은 문서 정합성 검증·8개 부서 라우팅·RTM(Must/Should/Optional) 판정에 집중합니다.
4. **부서별 권한 분리 (RBAC)**: 연구개발·품질·생산 부서와 구매·재무 부서 간 단가/원가 정보 노출 범위를 제어합니다.
5. **사전원가 (Pre-cost) Go/No-Go 모델**: RFQ 품목·최소 수량·고정비(금형/개발비)·변동비를 기반으로 대략적 단위 원가를 산출해 입찰 참여 의사결정을 지원합니다.

---

## 저장소 구조

```text
AutoRFx/
├── docs/                # 시스템 아키텍처, RTM 평가룰셋 및 회의 설계 문서
├── data/                # 비식별 샘플 메타데이터 및 평가용 Golden Dataset (원본 대용량 파일 Git 제외)
├── src/
│   ├── parser/          # 원본 불변 레이아웃 인식 파서 (PyMuPDF / python-docx)
│   ├── chunker/         # 조항 번호 계층 보존 청커 (Hierarchical Clause Chunker)
│   ├── rag/             # BGE-M3 / OpenAI 임베딩 + ChromaDB + LangGraph 라우터 & Citation Enforcer
│   ├── precost/         # 사전원가(Pre-cost) Go/No-Go 산출 로직
│   └── ui/              # Streamlit 대시보드 (부서별 RBAC 뷰 · RAG 챗봇 · 원가 시뮬레이터)
├── .env.example
└── pyproject.toml
```

---

## 빠른 시작

```bash
# 1. 환경 변수 설정
cp .env.example .env

# 2. 의존성 설치 (uv 기준)
uv sync

# 3. 대시보드 실행 (구현 후)
uv run streamlit run src/ui/app.py
```

---

## 팀 & R&R

자세한 팀원 역할 및 조직 소개는 [Sogang-RFx 프로필 페이지](https://github.com/Sogang-RFx)에서 확인할 수 있습니다.
