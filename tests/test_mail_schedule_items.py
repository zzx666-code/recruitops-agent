from datetime import date, time
from sqlalchemy import select
import pytest

from packages.recruitment_mail.scheduling import grounded_time, ensure_mail_schedule
from packages.recruitment_mail.processing import process_pending_mail
from packages.recruitment_mail.model_analysis import MailAnalysisProposal
from packages.recruitment_mail.storage import RecruitmentMailRecord
from packages.storage.models import ScheduleEventSnapshot, ApplicationSnapshot
from tests.test_mail_model_processing import setup_case, Client


@pytest.mark.parametrize("value,source,expected", [
    ("2026年09月21日 周一 17:01", "截止2026年09月21日 周一 17:01", (date(2026,9,21),time(17,1))),
    ("2026-10-14 03:37", "2026年10月14日 周三 03:37", (date(2026,10,14),time(3,37))),
    ("2026-09-21 24:00", "2026年9月21日 24:00", (date(2026,9,22),time(0))),
    ("2026-09-21", "截止2026年9月21日", (date(2026,9,21),None)),
    ("5个自然日", "5个自然日", (None,None)),
    ("2026-09-21 17:00", "2026-09-21 18:00", (None,None)),
    ("9月21日", "9月21日", (None,None)),
    ("2026-02-30", "2026-02-30", (None,None)),
])
def test_time_requires_explicit_source(value, source, expected):
    assert grounded_time(value, source) == expected


def items(store):
    with store.storage.session() as session:
        return list(session.scalars(select(ScheduleEventSnapshot)))


def test_assessment_creates_undated_task_does_not_change_stage_or_repeat():
    store, repo, record, settings, triage, proposal = setup_case()
    client = Client([triage, proposal])
    result = process_pending_mail(store, repo, settings, client=client)
    assert result["schedule_items_created"] == 1
    row = items(store)[0]
    assert row.event_date is None and row.time_kind == "unspecified"
    assert row.application_id == "4" and row.status == "pending"
    with store.storage.write_transaction() as session:
        assert session.get(ApplicationSnapshot,"4").stage == "applied"
        session.get(ScheduleEventSnapshot,row.id).status = "completed"
        mail = session.get(RecruitmentMailRecord, record.id)
        mail.processing_status = "pending"
        mail.raw_metadata = {k:v for k,v in mail.raw_metadata.items() if k != "model_processing"}
    result = process_pending_mail(store, repo, settings, client=Client([triage,proposal]))
    assert result["schedule_items_created"] == 0
    assert len(items(store)) == 1 and items(store)[0].status == "completed"


def test_local_task_mode_can_create_mail_task_without_global_writes_or_stage_change():
    store, repo, record, settings, triage, proposal = setup_case("written_test")
    settings.write_enabled = False
    settings.local_mail_tasks_enabled = True
    result = process_pending_mail(store, repo, settings, client=Client([triage, proposal]), tasks_only=True)
    assert result["schedule_items_created"] == 1
    assert items(store)[0].source_ref == record.id
    with store.storage.session() as session:
        assert session.get(ApplicationSnapshot, "4").stage == "applied"
    assert store.get(record.id).processing_status == "task_processed"
    assert process_pending_mail(store, repo, settings, client=Client([]))["reason"] == "write_disabled"


def test_task_inbox_does_not_prevent_later_application_status_processing():
    store, repo, record, settings, triage, proposal = setup_case("written_test")
    settings.local_mail_tasks_enabled = True

    task_result = process_pending_mail(store, repo, settings, client=Client([triage, proposal]), tasks_only=True)
    assert task_result["scope_complete"] is True
    assert store.get(record.id).processing_status == "task_processed"
    assert len(items(store)) == 1

    normal_result = process_pending_mail(store, repo, settings, client=Client([triage, proposal]))
    assert normal_result["processed"] == 1
    with store.storage.session() as session:
        assert session.get(ApplicationSnapshot, "4").stage == "written"
    assert len(items(store)) == 1


def test_task_inbox_irrelevant_triage_does_not_hide_mail_from_normal_processing():
    store, repo, record, settings, triage, proposal = setup_case("application_confirmation")
    settings.local_mail_tasks_enabled = True
    task_triage = [dict(triage[0], relevance="irrelevant")]

    task_result = process_pending_mail(store, repo, settings, client=Client([task_triage]), tasks_only=True)
    assert task_result["scope_complete"] is True
    assert store.get(record.id).processing_status == "task_processed"

    normal_result = process_pending_mail(store, repo, settings, client=Client([triage, proposal]))
    assert normal_result["processed"] == 1
    assert store.get(record.id).processing_status == "processed"


@pytest.mark.parametrize("event", ["information", "action_required", "application_confirmation"])
def test_task_inbox_excludes_non_test_or_interview_actions(event):
    store, repo, record, settings, triage, proposal = setup_case(event)
    settings.local_mail_tasks_enabled = True
    result = process_pending_mail(store, repo, settings, client=Client([triage, proposal]), tasks_only=True)
    assert result["schedule_items_created"] == 0
    assert items(store) == []


def test_company_invitation_creates_task_without_forcing_job_binding():
    store, repo, record, settings, triage, proposal = setup_case()
    proposal.update(job_title=None,candidate_application_id=None)
    result = process_pending_mail(store,repo,settings,client=Client([triage,proposal]))
    assert result["reminders"] == 1
    assert items(store)[0].application_id is None
    assert store.get(record.id).processing_status == "processed"


@pytest.mark.parametrize("event", ["information", "application_confirmation", "rejection"])
def test_non_action_events_do_not_create_calendar_entries(event):
    store, repo, record, settings, triage, proposal = setup_case(event)
    process_pending_mail(store,repo,settings,client=Client([triage,proposal]))
    assert items(store) == []


def test_claim_loss_prevents_calendar_write():
    store, repo, record, settings, triage, proposal = setup_case()
    with pytest.raises(ValueError,match="processing_claim_lost"):
        ensure_mail_schedule(store,record,MailAnalysisProposal(**proposal),"wrong-owner")
    assert items(store) == []
