import Std

/-!
# Existing-HMAC matching: an executable abstract specification

`mac` and `encode` are parameters, not implementations of SHA-256 or Python JSON.
The theorems hold for every such function. They therefore establish functional
facts and limitations, but do not establish pseudorandomness or 2PC security.
The source payload used in some proof hypotheses is a logical witness; the
executable `Source` structure does not require A to retain that payload.
-/

set_option autoImplicit false

namespace PatientMatching.Core

universe u v w x

structure Candidate (Data : Type u) where
  uuid : Nat
  data : Data
  deriving DecidableEq, Repr

structure Source (Key : Type v) (Tag : Type w) where
  uuid : Nat
  center : String
  epoch : Nat
  key : Key
  target : Tag
  deriving Repr

variable {Data : Type u} {Key : Type v} {Tag : Type w} {Payload : Type x}

/-- Reconstruct the candidate using the *source* center and source key. -/
def matchCandidate [DecidableEq Tag]
    (mac : Key → Payload → Tag) (encode : Data → String → Payload)
    (source : Source Key Tag) (row : Candidate Data) : Bool :=
  decide (mac source.key (encode row.data source.center) = source.target)

theorem matches_iff_tag_eq [DecidableEq Tag]
    (mac : Key → Payload → Tag) (encode : Data → String → Payload)
    (source : Source Key Tag) (row : Candidate Data) :
    matchCandidate mac encode source row = true ↔
      mac source.key (encode row.data source.center) = source.target := by
  simp [matchCandidate]

theorem equal_payload_matches [DecidableEq Tag]
    (mac : Key → Payload → Tag) (encode : Data → String → Payload)
    (source : Source Key Tag) (row : Candidate Data) (original : Data)
    (targetBound : source.target = mac source.key (encode original source.center))
    (samePayload : encode row.data source.center = encode original source.center) :
    matchCandidate mac encode source row = true := by
  simp [matchCandidate, samePayload, targetBound]

/-- Changing an unbound candidate UUID is invisible to the HMAC comparison. -/
theorem candidate_uuid_substitution [DecidableEq Tag]
    (mac : Key → Payload → Tag) (encode : Data → String → Payload)
    (source : Source Key Tag) (row : Candidate Data) (replacement : Nat) :
    matchCandidate mac encode source { row with uuid := replacement } =
      matchCandidate mac encode source row := rfl

/-- The source UUID is also absent from the patient HMAC input. -/
theorem source_uuid_substitution [DecidableEq Tag]
    (mac : Key → Payload → Tag) (encode : Data → String → Payload)
    (source : Source Key Tag) (row : Candidate Data) (replacement : Nat) :
    matchCandidate mac encode { source with uuid := replacement } row =
      matchCandidate mac encode source row := rfl

/-- An epoch label alone does not authenticate which key was provided. -/
theorem epoch_label_substitution [DecidableEq Tag]
    (mac : Key → Payload → Tag) (encode : Data → String → Payload)
    (source : Source Key Tag) (row : Candidate Data) (replacement : Nat) :
    matchCandidate mac encode { source with epoch := replacement } row =
      matchCandidate mac encode source row := rfl

def Collision (mac : Key → Payload → Tag) (key : Key)
    (left right : Payload) : Prop :=
  left ≠ right ∧ mac key left = mac key right

/-- A tag match entails equal payloads OR an actual collision, not equal persons. -/
theorem match_implies_equal_payload_or_collision [DecidableEq Tag]
    (mac : Key → Payload → Tag) (encode : Data → String → Payload)
    (source : Source Key Tag) (row : Candidate Data) (original : Data)
    (targetBound : source.target = mac source.key (encode original source.center))
    (matched : matchCandidate mac encode source row = true) :
    encode row.data source.center = encode original source.center ∨
      Collision mac source.key (encode row.data source.center)
        (encode original source.center) := by
  classical
  have tagEq := (matches_iff_tag_eq mac encode source row).mp matched
  by_cases same : encode row.data source.center = encode original source.center
  · exact Or.inl same
  · exact Or.inr ⟨same, tagEq.trans targetBound⟩

/-- This conclusion explicitly requires absence of the particular collision. -/
theorem payload_equality_of_match_and_no_collision [DecidableEq Tag]
    (mac : Key → Payload → Tag) (encode : Data → String → Payload)
    (source : Source Key Tag) (row : Candidate Data) (original : Data)
    (targetBound : source.target = mac source.key (encode original source.center))
    (matched : matchCandidate mac encode source row = true)
    (noCollision : ¬ Collision mac source.key (encode row.data source.center)
      (encode original source.center)) :
    encode row.data source.center = encode original source.center := by
  rcases match_implies_equal_payload_or_collision mac encode source row original
      targetBound matched with same | collision
  · exact same
  · exact False.elim (noCollision collision)

def matchingIds [DecidableEq Tag]
    (mac : Key → Payload → Tag) (encode : Data → String → Payload)
    (source : Source Key Tag) (rows : List (Candidate Data)) : List Nat :=
  (rows.filter (matchCandidate mac encode source)).map Candidate.uuid

theorem matchingIds_membership [DecidableEq Tag]
    (mac : Key → Payload → Tag) (encode : Data → String → Payload)
    (source : Source Key Tag) (rows : List (Candidate Data)) (id : Nat) :
    id ∈ matchingIds mac encode source rows ↔
      ∃ row, row ∈ rows ∧ matchCandidate mac encode source row = true ∧ row.uuid = id := by
  simp only [matchingIds, List.mem_map, List.mem_filter]
  constructor
  · rintro ⟨row, ⟨member, matched⟩, sameId⟩
    exact ⟨row, member, matched, sameId⟩
  · rintro ⟨row, member, matched, sameId⟩
    exact ⟨row, ⟨member, matched⟩, sameId⟩

inductive Result where
  | noMatch
  | singleMatch (uuid : Nat)
  | ambiguousMatches
  | abortedOrIncomplete
  | keyEpochUnavailable
  deriving DecidableEq, Repr

/-- Count matched records, retaining duplicate tuples and even repeated UUIDs. -/
def classify : List Nat → Result
  | [] => .noMatch
  | [uuid] => .singleMatch uuid
  | _ :: _ :: _ => .ambiguousMatches

theorem classify_noMatch_iff (ids : List Nat) :
    classify ids = .noMatch ↔ ids = [] := by
  cases ids with
  | nil => simp [classify]
  | cons first tail =>
    cases tail <;> simp [classify]

theorem classify_singleMatch_iff (ids : List Nat) (uuid : Nat) :
    classify ids = .singleMatch uuid ↔ ids = [uuid] := by
  cases ids with
  | nil => simp [classify]
  | cons first tail =>
    cases tail <;> simp [classify]

theorem classify_ambiguous_iff (ids : List Nat) :
    classify ids = .ambiguousMatches ↔ 2 ≤ ids.length := by
  cases ids with
  | nil => simp [classify]
  | cons first tail =>
    cases tail <;> simp [classify]

/-- Ideal completed-batch interface. No computation transcript is modelled here. -/
def run (complete epochAvailable : Bool) (ids : List Nat) : Result :=
  if complete then
    if epochAvailable then classify ids else .keyEpochUnavailable
  else .abortedOrIncomplete

theorem incomplete_is_abort (epochAvailable : Bool) (ids : List Nat) :
    run false epochAvailable ids = .abortedOrIncomplete := rfl

theorem missing_epoch_is_error (ids : List Nat) :
    run true false ids = .keyEpochUnavailable := rfl

theorem run_noMatch_iff (complete epochAvailable : Bool) (ids : List Nat) :
    run complete epochAvailable ids = .noMatch ↔
      complete = true ∧ epochAvailable = true ∧ ids = [] := by
  cases complete <;> cases epochAvailable <;>
    simp [run, classify_noMatch_iff]

theorem run_singleMatch_iff (complete epochAvailable : Bool)
    (ids : List Nat) (uuid : Nat) :
    run complete epochAvailable ids = .singleMatch uuid ↔
      complete = true ∧ epochAvailable = true ∧ ids = [uuid] := by
  cases complete <;> cases epochAvailable <;>
    simp [run, classify_singleMatch_iff]

theorem run_ambiguous_iff (complete epochAvailable : Bool) (ids : List Nat) :
    run complete epochAvailable ids = .ambiguousMatches ↔
      complete = true ∧ epochAvailable = true ∧ 2 ≤ ids.length := by
  cases complete <;> cases epochAvailable <;>
    simp [run, classify_ambiguous_iff]

theorem single_candidate_completeness [DecidableEq Tag]
    (mac : Key → Payload → Tag) (encode : Data → String → Payload)
    (source : Source Key Tag) (row : Candidate Data) (original : Data)
    (targetBound : source.target = mac source.key (encode original source.center))
    (samePayload : encode row.data source.center = encode original source.center) :
    run true true (matchingIds mac encode source [row]) = .singleMatch row.uuid := by
  have matched := equal_payload_matches mac encode source row original targetBound samePayload
  simp [run, matchingIds, matched, classify]

/-- Generic impossibility: identical encodings of distinct people defeat ANY
deterministic fingerprint as an unambiguous person identifier. -/
theorem demographic_identifier_impossible
    {Person : Type u} (encodePerson : Person → Payload)
    (fingerprint : Payload → Tag)
    (homonyms : ∃ p q : Person, p ≠ q ∧ encodePerson p = encodePerson q) :
    ¬ (∀ p q : Person,
      fingerprint (encodePerson p) = fingerprint (encodePerson q) → p = q) := by
  intro identifies
  obtain ⟨p, q, different, sameEncoding⟩ := homonyms
  exact different (identifies p q (congrArg fingerprint sameEncoding))

/-- A two-person witness: identity is the first component, demographics the
second. This is an information countermodel, not a toy cryptographic protocol. -/
theorem concrete_homonyms
    (fingerprint : String → Tag) :
    ∃ p q : Nat × String, p.1 ≠ q.1 ∧ p.2 = q.2 ∧
      fingerprint p.2 = fingerprint q.2 := by
  exact ⟨(1, "same normalized demographics"),
    (2, "same normalized demographics"), by decide, rfl, rfl⟩

/-- Aggregate the match vector without revealing individual ambiguous matches. -/
def summarizeBits (bits : List Bool) : Result :=
  classify ((bits.zipIdx.filter (fun item => item.1)).map (fun item => item.2))

theorem full_vector_exposes_more_than_summary :
    summarizeBits [true, true, false, false] =
      summarizeBits [false, false, true, true] ∧
    ([true, true, false, false] : List Bool) ≠ [false, false, true, true] := by
  decide

/-- There is no function recovering every full vector from the summary alone.
This concerns the abstract summary function; comparator-realizable worlds are
constructed separately in `PatientMatching.Counterexamples`. -/
theorem no_full_vector_reconstructor :
    ¬ ∃ recover : Result → List Bool,
      ∀ bits, recover (summarizeBits bits) = bits := by
  rintro ⟨recover, recovers⟩
  obtain ⟨sameSummary, differentBits⟩ := full_vector_exposes_more_than_summary
  apply differentBits
  exact (recovers _).symm.trans ((congrArg recover sameSummary).trans (recovers _))

end PatientMatching.Core
