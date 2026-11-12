"""An org: roles, the links between them and its limits, written as YAML (the Org page shows and edits it).

Nothing in keel knows a "CEO" or a "tech lead": a role is a name, an agent and the workflows it may run, and a link
says what may pass from one role to another (delegate, report, ask, approve, notify). keel checks every hand-over
against the links (may), so a role can only give work to the roles the person linked it to.

    parse(text) -> Org            raises OrgError with every problem it found (problems)
    may(org, src, dst, kind)      does a link let src do kind to dst
    team(org, role)               the roles src may delegate to, in the order the links name them
    boss(org, role)               the role the given one reports to (None: the person at the top)
"""

from __future__ import annotations

import re
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

KINDS = ("delegate", "report", "ask", "approve", "notify")
AUTONOMY = ("watch", "propose", "act")
ROLE_ID = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")
YAML_MAX = 64_000


class OrgError(Exception):
    status = 400

    def __init__(self, problems: list[str]):
        self.problems = problems
        self.hint = "Fix these lines in the org's YAML and save again."
        super().__init__("The org has problems: " + "; ".join(problems))


class Budget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tokens_month: int | None = Field(default=None, ge=0)


class Role(BaseModel):
    model_config = ConfigDict(extra="forbid")
    human: bool = False
    title: str | None = Field(default=None, max_length=80)
    # the agent this role is, shown on the chart (O1: its workflows name the agents their steps run)
    agent: str | None = Field(default=None, max_length=80)
    workflows: list[str] = Field(default_factory=list)
    # the flow it runs first when it gets a mission (default: the first of its workflows)
    plan: str | None = None
    # the flow it runs when every mission it gave out has ended (none: its report is its children's reports)
    review: str | None = None
    autonomy: Literal["watch", "propose", "act"] = "propose"
    # the kinds of questions it may answer for its team without asking up (the escalation chain, O2)
    may_decide: list[str] = Field(default_factory=list)
    budget: Budget | None = None

    def first_flow(self) -> str | None:
        return self.plan or (self.workflows[0] if self.workflows else None)


def _names(v) -> list[str]:
    if isinstance(v, str):
        return [v]
    if isinstance(v, list) and all(isinstance(x, str) for x in v):
        return list(v)
    raise ValueError("a role name or a list of role names")


class Link(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    src: list[str] = Field(alias="from")
    dst: list[str] = Field(alias="to")
    can: list[Literal["delegate", "report", "ask", "approve", "notify"]]

    @field_validator("src", "dst", mode="before")
    @classmethod
    def _list(cls, v):
        return _names(v)


class Limits(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_running: int = Field(default=3, ge=1, le=20)
    depth: int = Field(default=4, ge=1, le=8)
    children: int = Field(default=8, ge=1, le=20)
    send_backs: int = Field(default=3, ge=0, le=10)
    escalation_hops: int = Field(default=3, ge=0, le=8)


class Org(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="", max_length=80)
    projects: list[str] = Field(default_factory=list)
    limits: Limits = Field(default_factory=Limits)
    roles: dict[str, Role]
    links: list[Link] = Field(default_factory=list)
    # wake-ups (Jira, CI, schedules) come in O3; kept as written so an org made for them already loads
    wake: dict | None = None


def _where(loc) -> str:
    return ".".join(str(x) for x in loc) or "the org"


def parse(text: str) -> Org:
    """The org in this YAML, checked: each role and link, and that the links and flows name roles and workflows that
    are there. Every problem at once, each with where it is."""
    if len(text or "") > YAML_MAX:
        raise OrgError([f"The YAML is longer than {YAML_MAX} characters."])
    try:
        raw = yaml.safe_load(text or "")
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        at = f" (line {mark.line + 1})" if mark else ""
        raise OrgError([f"This is not valid YAML{at}: {getattr(exc, 'problem', None) or exc}"]) from None
    if not isinstance(raw, dict):
        raise OrgError(["The org is a YAML mapping with roles: and links:."])
    try:
        org = Org.model_validate(raw)
    except ValidationError as exc:
        raise OrgError([f"{_where(e['loc'])}: {e['msg']}" for e in exc.errors()]) from None
    problems = []
    for rid, role in org.roles.items():
        if not ROLE_ID.match(rid):
            problems.append(f"roles.{rid}: a role name is lowercase letters, digits, - and _ (at most 40)")
        if role.human:
            if role.agent or role.workflows:
                problems.append(f"roles.{rid}: a human role has no agent and no workflows")
            continue
        if not role.workflows:
            problems.append(f"roles.{rid}: name the workflows it may run (workflows: [feature, fix])")
        for key in ("plan", "review"):
            wf = getattr(role, key)
            if wf and wf not in role.workflows:
                problems.append(f"roles.{rid}.{key}: {wf} is not in its workflows")
    for n, link in enumerate(org.links):
        for side, names in (("from", link.src), ("to", link.dst)):
            for name in names:
                if name not in org.roles:
                    problems.append(f"links.{n}.{side}: there is no role {name}")
        if "delegate" in link.can:
            for name in link.dst:
                r = org.roles.get(name)
                if r is not None and r.human:
                    problems.append(f"links.{n}: work cannot be delegated to {name}, a human role (link it with ask or report)")
    if problems:
        raise OrgError(problems)
    return org


def may(org: Org, src: str, dst: str, kind: str) -> bool:
    return any(src in link.src and dst in link.dst and kind in link.can for link in org.links)


def team(org: Org, role: str) -> list[str]:
    out: list[str] = []
    for link in org.links:
        if role in link.src and "delegate" in link.can:
            out += [d for d in link.dst if d not in out and d != role]
    return out


def boss(org: Org, role: str) -> str | None:
    """Who gave work to this role in the links (the first one), else None."""
    for link in org.links:
        if role in link.dst and "delegate" in link.can:
            return link.src[0]
    return None


def humans(org: Org) -> list[str]:
    return [rid for rid, r in org.roles.items() if r.human]


EXAMPLE = """\
# An org is only roles and links: draw any shape. Work goes down a delegate link; reports come up a report link.
name: Shop team
projects: [shop]
limits: { max_running: 3, depth: 4, children: 8, send_backs: 3 }

roles:
  me:
    human: true
  lead:
    title: Tech lead
    agent: org-tech-lead
    workflows: [lead-intake, lead-review]
    plan: lead-intake
    review: lead-review
    autonomy: propose
  fe:
    title: Frontend engineer
    agent: implementer
    workflows: [small, feature, fix]
    autonomy: act
  be:
    title: Backend engineer
    agent: implementer
    workflows: [small, feature, fix]
    autonomy: act

links:
  - { from: me, to: lead, can: [delegate] }
  - { from: lead, to: [fe, be], can: [delegate] }
  - { from: [fe, be], to: lead, can: [report, ask] }
  - { from: lead, to: me, can: [report, ask, approve] }
"""
