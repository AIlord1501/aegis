from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession, LLMDependency
from app.llm.types import LLMError
from app.models.intelligence import InvestigationReport
from app.schemas.intelligence import InvestigationReportRead
from app.services.analysis import analyze_incident

router = APIRouter(prefix="/api/v1", tags=["intelligence"])


@router.get("/intelligence/model/health")
async def model_health(llm: LLMDependency) -> dict[str, str]:
    try:
        return await llm.health()
    except LLMError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc


@router.post("/incidents/{incident_id}/analysis", response_model=InvestigationReportRead, status_code=status.HTTP_201_CREATED)
async def create_incident_analysis(incident_id: int, current_user: CurrentUser, db: DbSession, llm: LLMDependency) -> InvestigationReport:
    try:
        return await analyze_incident(db, owner_id=current_user.id, incident_id=incident_id, llm=llm)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except LLMError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Configured model is unavailable") from exc


@router.get("/incidents/{incident_id}/reports", response_model=list[InvestigationReportRead])
async def list_incident_reports(incident_id: int, current_user: CurrentUser, db: DbSession) -> list[InvestigationReport]:
    reports = await db.scalars(select(InvestigationReport).where(InvestigationReport.incident_id == incident_id, InvestigationReport.owner_id == current_user.id).order_by(InvestigationReport.created_at.desc()))
    return list(reports)


@router.get("/investigation-reports/{report_id}", response_model=InvestigationReportRead)
async def get_investigation_report(report_id: int, current_user: CurrentUser, db: DbSession) -> InvestigationReport:
    report = await db.scalar(select(InvestigationReport).where(InvestigationReport.id == report_id, InvestigationReport.owner_id == current_user.id))
    if report is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Investigation report not found")
    return report
