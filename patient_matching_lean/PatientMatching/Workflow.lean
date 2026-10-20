import Std

/-!
# A concrete, guarded result-acceptance workflow

This module proves properties of an executable acceptance function, not of HMAC,
2PC, signatures, databases, or clinical identity. `Nat` reference fields are
opaque typed-model identifiers; they are not proved cryptographic commitments.
The caller supplies an authorised expected request and immutable row snapshots.
An externally supplied signature verifier has an explicit soundness obligation.
Even an authentic reply need not describe an honest computation: that additional
protocol obligation belongs to the cryptographic implementation.
-/

namespace PatientMatching.Workflow

structure Metadata where
  sessionId : Nat
  sourceCentre : Nat
  receiverCentre : Nat
  audience : Nat
  purpose : Nat
  schema : Nat
  keyEpoch : Nat
  circuit : Nat
  batchRef : Nat
  deriving DecidableEq, Repr

structure SourceRef where
  uuid : Nat
  rowVersion : Nat
  fingerprintRef : Nat
  centre : Nat
  schema : Nat
  keyEpoch : Nat
  deriving DecidableEq, Repr

structure CandidateRef where
  uuid : Nat
  rowVersion : Nat
  payloadRef : Nat
  centre : Nat
  deriving DecidableEq, Repr

structure Request where
  meta : Metadata
  source : SourceRef
  candidates : List CandidateRef
  deriving DecidableEq, Repr

inductive Outcome where
  | single (candidate : CandidateRef)
  | noMatch
  | ambiguous (candidates : List CandidateRef)
  | incomplete
  | epochUnavailable
  deriving DecidableEq, Repr

structure Reply where
  meta : Metadata
  source : SourceRef
  outcome : Outcome
  deriving DecidableEq, Repr

structure Link where
  sessionId : Nat
  source : SourceRef
  candidate : CandidateRef
  deriving DecidableEq, Repr

structure State where
  sourceRows : Nat → Option SourceRef
  candidateRows : Nat → Option CandidateRef
  availableEpochs : List Nat
  usedSessionIds : List Nat
  links : List Link

/-- These are actual structural equalities and snapshot lookups. There is no
    Boolean named `inputsVerified` standing in for record binding. -/
def Checks (verifySignature : Reply → Bool) (expected : Request)
    (state : State) (reply : Reply) (candidate : CandidateRef) : Prop :=
  verifySignature reply = true ∧
  reply.meta = expected.meta ∧
  reply.source = expected.source ∧
  state.sourceRows expected.source.uuid = some expected.source ∧
  state.candidateRows candidate.uuid = some candidate ∧
  candidate ∈ expected.candidates ∧
  expected.source.centre = expected.meta.sourceCentre ∧
  expected.source.schema = expected.meta.schema ∧
  expected.source.keyEpoch = expected.meta.keyEpoch ∧
  candidate.centre = expected.meta.receiverCentre ∧
  expected.meta.keyEpoch ∈ state.availableEpochs ∧
  expected.meta.sessionId ∉ state.usedSessionIds

instance (verifySignature : Reply → Bool) (expected : Request)
    (state : State) (reply : Reply) (candidate : CandidateRef) :
    Decidable (Checks verifySignature expected state reply candidate) := by
  unfold Checks
  infer_instance

def recordLink (expected : Request) (state : State)
    (candidate : CandidateRef) : State :=
  { state with
    usedSessionIds := expected.meta.sessionId :: state.usedSessionIds
    links := ⟨expected.meta.sessionId, expected.source, candidate⟩ :: state.links }

/-- `none` is rejection: it does not update the supplied state. An accepted
    reply adds an edge and consumes the session; it never merges patient rows. -/
def accept (verifySignature : Reply → Bool) (expected : Request)
    (state : State) (reply : Reply) : Option State :=
  match reply.outcome with
  | .single candidate =>
      if Checks verifySignature expected state reply candidate then
        some (recordLink expected state candidate)
      else none
  | _ => none

/-- Complete characterisation of every successful transition. -/
theorem accepted_iff (verifySignature : Reply → Bool) (expected : Request)
    (state next : State) (reply : Reply) :
    accept verifySignature expected state reply = some next ↔
      ∃ candidate, reply.outcome = .single candidate ∧
        Checks verifySignature expected state reply candidate ∧
        recordLink expected state candidate = next := by
  cases ho : reply.outcome <;> simp [accept, ho]

theorem accepted_inputs_are_bound (verifySignature : Reply → Bool)
    (expected : Request) (state next : State) (reply : Reply)
    (h : accept verifySignature expected state reply = some next) :
    reply.meta = expected.meta ∧ reply.source = expected.source ∧
    state.sourceRows expected.source.uuid = some expected.source ∧
    ∃ candidate, reply.outcome = .single candidate ∧
      state.candidateRows candidate.uuid = some candidate ∧
      candidate ∈ expected.candidates := by
  obtain ⟨candidate, ho, hc, _⟩ := (accepted_iff _ _ _ _ _).mp h
  exact ⟨hc.2.1, hc.2.2.1, hc.2.2.2.1,
    candidate, ho, hc.2.2.2.2.1, hc.2.2.2.2.2.1⟩

theorem accepted_context_is_bound (verifySignature : Reply → Bool)
    (expected : Request) (state next : State) (reply : Reply)
    (h : accept verifySignature expected state reply = some next) :
    expected.source.centre = expected.meta.sourceCentre ∧
    expected.source.schema = expected.meta.schema ∧
    expected.source.keyEpoch = expected.meta.keyEpoch ∧
    expected.meta.keyEpoch ∈ state.availableEpochs ∧
    ∃ candidate, reply.outcome = .single candidate ∧
      candidate.centre = expected.meta.receiverCentre := by
  obtain ⟨candidate, ho, hc, _⟩ := (accepted_iff _ _ _ _ _).mp h
  obtain ⟨_, _, _, _, _, _, hcentre, hschema, hepoch, hreceiver, havailable, _⟩ := hc
  exact ⟨hcentre, hschema, hepoch, havailable, candidate, ho, hreceiver⟩

theorem accepted_session_is_fresh (verifySignature : Reply → Bool)
    (expected : Request) (state next : State) (reply : Reply)
    (h : accept verifySignature expected state reply = some next) :
    expected.meta.sessionId ∉ state.usedSessionIds := by
  obtain ⟨_, _, hc, _⟩ := (accepted_iff _ _ _ _ _).mp h
  obtain ⟨_, _, _, _, _, _, _, _, _, _, _, hfresh⟩ := hc
  exact hfresh

theorem accepted_session_is_consumed (verifySignature : Reply → Bool)
    (expected : Request) (state next : State) (reply : Reply)
    (h : accept verifySignature expected state reply = some next) :
    expected.meta.sessionId ∈ next.usedSessionIds := by
  obtain ⟨candidate, _, _, hn⟩ := (accepted_iff _ _ _ _ _).mp h
  rw [← hn]
  simp [recordLink]

/-- Reject any reuse of an already consumed session, even with a different
    request, reply, or verifier. Persistent, atomic state storage is external. -/
theorem used_session_rejected (verifySignature : Reply → Bool)
    (expected : Request) (state : State) (reply : Reply)
    (used : expected.meta.sessionId ∈ state.usedSessionIds) :
    accept verifySignature expected state reply = none := by
  cases ho : reply.outcome <;> simp [accept, ho, Checks, used]

theorem replay_rejected (verifySignature verifySignature' : Reply → Bool)
    (expected expected' : Request) (state next : State) (reply reply' : Reply)
    (h : accept verifySignature expected state reply = some next)
    (sameSession : expected'.meta.sessionId = expected.meta.sessionId) :
    accept verifySignature' expected' next reply' = none := by
  apply used_session_rejected
  rw [sameSession]
  exact accepted_session_is_consumed _ _ _ _ _ h

theorem accepted_rows_unchanged (verifySignature : Reply → Bool)
    (expected : Request) (state next : State) (reply : Reply)
    (h : accept verifySignature expected state reply = some next) :
    next.sourceRows = state.sourceRows ∧
    next.candidateRows = state.candidateRows ∧
    next.availableEpochs = state.availableEpochs := by
  obtain ⟨candidate, _, _, hn⟩ := (accepted_iff _ _ _ _ _).mp h
  rw [← hn]
  exact ⟨rfl, rfl, rfl⟩

theorem accepted_link_has_original_references (verifySignature : Reply → Bool)
    (expected : Request) (state next : State) (reply : Reply)
    (h : accept verifySignature expected state reply = some next) :
    ∃ candidate, reply.outcome = .single candidate ∧
      next.links = ⟨expected.meta.sessionId, expected.source, candidate⟩ :: state.links := by
  obtain ⟨candidate, ho, _, hn⟩ := (accepted_iff _ _ _ _ _).mp h
  exact ⟨candidate, ho, congrArg State.links hn.symm⟩

theorem existing_links_preserved (verifySignature : Reply → Bool)
    (expected : Request) (state next : State) (reply : Reply) (link : Link)
    (h : accept verifySignature expected state reply = some next)
    (old : link ∈ state.links) : link ∈ next.links := by
  obtain ⟨_, _, hn⟩ := accepted_link_has_original_references _ _ _ _ _ h
  rw [hn]
  exact List.mem_cons_of_mem _ old

theorem non_single_rejected (verifySignature : Reply → Bool)
    (expected : Request) (state : State) (reply : Reply)
    (notSingle : ∀ candidate, reply.outcome ≠ .single candidate) :
    accept verifySignature expected state reply = none := by
  cases ho : reply.outcome with
  | single candidate => exact False.elim (notSingle candidate ho)
  | noMatch => simp [accept, ho]
  | ambiguous candidates => simp [accept, ho]
  | incomplete => simp [accept, ho]
  | epochUnavailable => simp [accept, ho]

theorem unavailable_epoch_rejected (verifySignature : Reply → Bool)
    (expected : Request) (state : State) (reply : Reply)
    (unavailable : expected.meta.keyEpoch ∉ state.availableEpochs) :
    accept verifySignature expected state reply = none := by
  cases ho : reply.outcome <;> simp [accept, ho, Checks, unavailable]

/-- Signature authenticity follows only if the external verifier is sound.
    This does not prove that a signer supplied genuine patient data or ran 2PC. -/
theorem accepted_authentic_of_verifier_sound (verifySignature : Reply → Bool)
    (Authentic : Reply → Prop)
    (verifierSound : ∀ reply, verifySignature reply = true → Authentic reply)
    (expected : Request) (state next : State) (reply : Reply)
    (h : accept verifySignature expected state reply = some next) :
    Authentic reply := by
  obtain ⟨_, _, hc, _⟩ := (accepted_iff _ _ _ _ _).mp h
  exact verifierSound reply hc.1

end PatientMatching.Workflow
