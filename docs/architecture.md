# AutoRFx System Architecture

```mermaid
flowchart TD
    A["원본 다국어 RFQ<br/>(불변 보관)"] --> B["레이아웃·조항 파서<br/>(Page, Clause, Line 보존)"]
    B --> C["번역 레이어<br/>(DeepL / LLM 전처리)"]
    B --> D["계층적 청커<br/>(Hierarchical Chunking)"]
    D --> E["VectorDB 인덱싱<br/>(BGE-M3 / ChromaDB)"]
    E --> F["LangGraph 라우팅 에이전트<br/>(8개 부서 + 독소조항 + RTM)"]
    F --> G["Citation Enforcer<br/>(원문 페이지·문장 역추적)"]
    F --> H["사전원가(Pre-cost) 산출<br/>(최소수량·고정비·변동비)"]
    G --> I["Streamlit 대시보드<br/>(부서별 RBAC 뷰 · RAG 챗봇)"]
    H --> I
```

## 핵심 아키텍처 결정 (ADR 요약)

- **원본 불변성**: 모든 청크는 `source_doc`, `page_number`, `section_id`, `sentence_index`를 메타데이터로 강제 유지합니다.
- **Citation 검증**: 최종 UI 응답은 원문 인덱스 매핑 검증을 통과한 경우에만 출력되며, 출처 확인 실패 시 경고 플래그를 반환합니다.
- **RBAC 단가 마스킹**: 사용자 부서 역할(영업·구매·재무 vs 연구·품질·생산)에 따라 금액·마진 관련 조항 노출을 필터링합니다.
