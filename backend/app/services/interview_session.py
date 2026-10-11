"""Persisted interview state: where an interview is, reconstructed from database rows alone.

Nothing is held in memory between requests, so this is what makes an interview survive a refresh,
a closed browser or a failed request. It is used to decide the next question, to resume an
interrupted interview, to enforce progress rules and to let interviewers inspect an interview that is
still in progress.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.db.models import Answer, Evaluation, Interview, InterviewQuestion, Question
from app.services.question_selector import FOLLOW_UP_CATEGORY

# Step states (what has happened to one asked question)
STATE_EVALUATED = "evaluated"      # answered and evaluated
STATE_SUBMITTED = "submitted"      # answered, evaluation not stored yet
STATE_PENDING = "pending"          # the question currently waiting for the candidate's answer
STATE_UNANSWERED = "unanswered"    # an earlier question that never received an answer

# What a client must do to carry on
ACTION_ANSWER = "answer"                # show the pending question
ACTION_EVALUATE = "evaluate"            # the last answer is saved but not evaluated
ACTION_NEXT_QUESTION = "next_question"  # the last answer is evaluated: ask what comes next (or finish)
ACTION_COMPLETE = "complete"            # the interview is already completed


@dataclass
class InterviewStep:
    """One asked question with its answer and evaluation (either may still be missing)."""
    interview_question: InterviewQuestion
    question: Question
    answer: Optional[Answer]
    evaluation: Optional[Evaluation]

    @property
    def is_follow_up(self) -> bool:
        return self.question.category == FOLLOW_UP_CATEGORY


def load_interview_steps(db: Session, interview_questions: list[InterviewQuestion]) -> list[InterviewStep]:
    steps = []
    for iq in interview_questions:
        question = db.query(Question).filter(Question.id == iq.question_id).first()
        answer = db.query(Answer).filter(Answer.interview_question_id == iq.id).first()
        evaluation = db.query(Evaluation).filter(Evaluation.answer_id == answer.id).first() if answer else None
        steps.append(InterviewStep(iq, question, answer, evaluation))
    return steps


def load_steps_for_interview(db: Session, interview_id: int) -> list[InterviewStep]:
    interview_questions = db.query(InterviewQuestion).filter(
        InterviewQuestion.interview_id == interview_id
    ).order_by(InterviewQuestion.question_order).all()
    return load_interview_steps(db, interview_questions)


def trailing_follow_ups(steps: list[InterviewStep]) -> int:
    """
    Follow-ups already asked for the CURRENT main question.

    The count is not held in memory: follow-ups are persisted as InterviewQuestion rows whose Question has
    category 'follow_up', and a follow-up only ever comes after its main question or an earlier follow-up,
    so the length of the trailing run of follow-ups is the count. It therefore survives every request.
    """
    count = 0
    for step in reversed(steps):
        if not step.is_follow_up:
            break
        count += 1
    return count


def main_question_scores(steps: list[InterviewStep]) -> list[float]:
    """Mean evaluation score of each main question together with its follow-ups."""
    groups: list[list[int]] = []
    for step in steps:
        if not step.is_follow_up or not groups:
            groups.append([])
        if step.evaluation is not None:
            groups[-1].append(step.evaluation.score)
    return [sum(scores) / len(scores) for scores in groups if scores]


def number_steps(steps: list[InterviewStep]) -> list[tuple[int, int]]:
    """(main question number, follow-up number) for every step; follow-up number is 0 for a main question."""
    numbers, main, follow_up = [], 0, 0
    for step in steps:
        if step.is_follow_up:
            follow_up += 1
        else:
            main, follow_up = main + 1, 0
        numbers.append((main, follow_up))
    return numbers


def step_state(step: InterviewStep, is_last: bool) -> str:
    if step.answer is None:
        return STATE_PENDING if is_last else STATE_UNANSWERED
    return STATE_EVALUATED if step.evaluation is not None else STATE_SUBMITTED


def next_action(steps: list[InterviewStep]) -> str:
    """What has to happen next for an in-progress interview, judged by its last asked question."""
    last = steps[-1]
    if last.answer is None:
        return ACTION_ANSWER
    if last.evaluation is None:
        return ACTION_EVALUATE
    return ACTION_NEXT_QUESTION


def last_activity(interview: Interview, steps: list[InterviewStep]) -> Optional[datetime]:
    """Most recent persisted event of the interview (a question asked, answered or evaluated)."""
    times = [interview.started_at or interview.created_at]
    for step in steps:
        times.append(step.interview_question.created_at)
        if step.answer:
            times.append(step.answer.created_at)
        if step.evaluation:
            times.append(step.evaluation.created_at)
    times = [t for t in times if t]
    return max(times) if times else None
