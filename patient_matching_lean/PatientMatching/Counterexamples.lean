import PatientMatching.Core
import PatientMatching.Workflow

/-!
# Comparator-realizable disclosure and an explicit backend-refinement bridge

The disclosure witness below uses one fixed candidate input and two source
inputs which differ only in their private target.  Its premise is that two
candidate payloads have different tags under the fixed source key and centre.
This premise is not a claim that all distinct payloads have distinct HMACs.

The optional shape theorem shows how equal candidate payload lengths (or other
public shapes) can be required without modelling Python's byte serializer.
Only the public fields represented here are covered; a concrete privacy game
must include all its actual public metadata and observations.

The final theorem couples an accepted reply to the Core computation only under
an explicit backend-refinement premise. Neither signatures nor structural
reference checks establish that premise.  No cryptographic backend is verified
or instantiated by this module.
-/

set_option autoImplicit false

namespace PatientMatching.Counterexamples

universe u v w x z

variable {Data : Type u} {Key : Type v} {Tag : Type w} {Payload : Type x}

/-- One fixed B input: two records per payload, with four distinct UUIDs. -/
def fixedCandidates (left right : Data) : List (Core.Candidate Data) :=
  [⟨1, left⟩, ⟨2, left⟩, ⟨3, right⟩, ⟨4, right⟩]

theorem fixed_candidate_uuids (left right : Data) :
    (fixedCandidates left right).map Core.Candidate.uuid = [1, 2, 3, 4] ∧
    ((fixedCandidates left right).map Core.Candidate.uuid).Nodup := by
  simp [fixedCandidates]

def sourceFor (mac : Key → Payload → Tag) (encode : Data → String → Payload)
    (source : Core.Source Key Tag) (data : Data) : Core.Source Key Tag :=
  { source with target := mac source.key (encode data source.center) }

/-- These are the public source fields represented in the Core model. -/
def publicSourceFields (source : Core.Source Key Tag) : Nat × String × Nat :=
  (source.uuid, source.center, source.epoch)

theorem source_context_unchanged
    (mac : Key → Payload → Tag) (encode : Data → String → Payload)
    (source : Core.Source Key Tag) (left right : Data) :
    publicSourceFields (sourceFor mac encode source left) =
      publicSourceFields (sourceFor mac encode source right) ∧
    (sourceFor mac encode source left).key =
      (sourceFor mac encode source right).key := ⟨rfl, rfl⟩

def matchVector [DecidableEq Tag]
    (mac : Key → Payload → Tag) (encode : Data → String → Payload)
    (source : Core.Source Key Tag) (rows : List (Core.Candidate Data)) : List Bool :=
  rows.map (Core.matchCandidate mac encode source)

/-- Both differing vectors are realizable by the actual abstract comparator,
    while B's complete input, A's key, and the modelled public fields stay fixed. -/
theorem realizable_match_vectors [DecidableEq Tag]
    (mac : Key → Payload → Tag) (encode : Data → String → Payload)
    (source : Core.Source Key Tag) (left right : Data)
    (differentTags : mac source.key (encode left source.center) ≠
      mac source.key (encode right source.center)) :
    matchVector mac encode (sourceFor mac encode source left)
      (fixedCandidates left right) = [true, true, false, false] ∧
    matchVector mac encode (sourceFor mac encode source right)
      (fixedCandidates left right) = [false, false, true, true] := by
  simp [matchVector, fixedCandidates, sourceFor, Core.matchCandidate,
    differentTags, Ne.symm differentTags]

/-- A concrete same-summary/different-vector witness with fixed candidate inputs.
    This establishes excess disclosure relative to this aggregate interface;
    it does not instantiate a protocol transcript or security reduction. -/
theorem realizable_aggregate_disclosure [DecidableEq Tag]
    (mac : Key → Payload → Tag) (encode : Data → String → Payload)
    (source : Core.Source Key Tag) (left right : Data)
    (differentTags : mac source.key (encode left source.center) ≠
      mac source.key (encode right source.center)) :
    Core.summarizeBits (matchVector mac encode (sourceFor mac encode source left)
      (fixedCandidates left right)) = .ambiguousMatches ∧
    Core.summarizeBits (matchVector mac encode (sourceFor mac encode source right)
      (fixedCandidates left right)) = .ambiguousMatches ∧
    matchVector mac encode (sourceFor mac encode source left)
      (fixedCandidates left right) ≠
    matchVector mac encode (sourceFor mac encode source right)
      (fixedCandidates left right) := by
  obtain ⟨leftVector, rightVector⟩ :=
    realizable_match_vectors mac encode source left right differentTags
  rw [leftVector, rightVector]
  decide

/-- Instantiate `shape` with encoded byte length when a concrete batch requires
    equal public lengths. That equal-length premise remains explicit. -/
theorem fixed_batch_has_uniform_shape {Shape : Type z}
    (encode : Data → String → Payload) (shape : Payload → Shape)
    (center : String) (left right : Data)
    (sameShape : shape (encode left center) = shape (encode right center)) :
    (fixedCandidates left right).map (fun row => shape (encode row.data center)) =
      List.replicate 4 (shape (encode left center)) := by
  simp [fixedCandidates, ← sameShape]

/-- Forget non-Core reference information only for comparing declared outcomes.
    This function is not a credential or a proof of what a backend executed. -/
def declaredCoreResult : Workflow.Outcome → Core.Result
  | .single candidate => .singleMatch candidate.uuid
  | .noMatch => .noMatch
  | .ambiguous _ => .ambiguousMatches
  | .incomplete => .abortedOrIncomplete
  | .epochUnavailable => .keyEpochUnavailable

/--
If the external backend's result is already proved to refine the Core computation
on these exact inputs, every accepted single reply denotes exactly one matching
list entry, with a complete run and available epoch.

The premise must be established for the actual private inputs, encoding, key,
candidate order, UUID bindings, and execution.  It is deliberately not inferred
from signature authenticity.  The theorem does not establish that the opaque
Workflow references commit to the supplied Core inputs.
-/
theorem accepted_single_is_unique_if_backend_refines [DecidableEq Tag]
    (mac : Key → Payload → Tag) (encode : Data → String → Payload)
    (source : Core.Source Key Tag) (rows : List (Core.Candidate Data))
    (complete epochAvailable : Bool)
    (verifySignature : Workflow.Reply → Bool) (expected : Workflow.Request)
    (state next : Workflow.State) (reply : Workflow.Reply)
    (backendRefinement : declaredCoreResult reply.outcome =
      Core.run complete epochAvailable (Core.matchingIds mac encode source rows))
    (accepted : Workflow.accept verifySignature expected state reply = some next) :
    ∃ candidate, reply.outcome = .single candidate ∧
      complete = true ∧ epochAvailable = true ∧
      Core.matchingIds mac encode source rows = [candidate.uuid] := by
  obtain ⟨candidate, outcome, _, _⟩ :=
    (Workflow.accepted_iff verifySignature expected state next reply).mp accepted
  have singleResult :
      Core.run complete epochAvailable (Core.matchingIds mac encode source rows) =
        .singleMatch candidate.uuid := by
    rw [outcome] at backendRefinement
    simpa [declaredCoreResult] using backendRefinement.symm
  have correct := (Core.run_singleMatch_iff complete epochAvailable
    (Core.matchingIds mac encode source rows) candidate.uuid).mp singleResult
  exact ⟨candidate, outcome, correct⟩

end PatientMatching.Counterexamples
