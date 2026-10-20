import Std

/-!
# Conditional privacy and the limits of a disclosed match result

This module does not implement, postulate, or prove the security of a 2PC
protocol.  Real executions and a simulator are explicit parameters.  In
particular, `SimulationBound` is a premise that a separate protocol proof must
establish for the implementation and the intended corruption model.

Every execution below uses the same finite coin space `Fin sampleSize`, with
each coin equally likely.  Consequently acceptance counts have a common
denominator.  When `sampleSize > 0`, dividing a count bound by `sampleSize`
gives the corresponding probability bound.  The kernel-checked theorems use
natural-number count inequalities, avoiding an unverified floating-point or
real-number calculation.

The simulator's type restricts its inputs to the corrupt party's own input
and the explicitly permitted leakage.  The real view should include every
observable item claimed to be protected by an instantiation: messages,
outputs, lengths, errors, and any timing information included in its model.
Neither that coverage nor the existence of the simulator follows from the
theorems here.
-/

set_option autoImplicit false

namespace PatientMatching.Privacy

universe uWorld uOwn uLeak uView

/-- Number of equally weighted coins on which a distinguisher accepts.

The explicit `sampleSize` and the dependent type `Fin sampleSize` ensure the
same number of samples is used for both worlds and for the simulator.
-/
def acceptanceCount {View : Type uView} (distinguisher : View → Bool) :
    (sampleSize : Nat) → (Fin sampleSize → View) → Nat
  | 0, _ => 0
  | size + 1, samples =>
      (if distinguisher (samples 0) then 1 else 0) +
        acceptanceCount distinguisher size (fun coin => samples coin.succ)

/-- Symmetric absolute-gap bound, expressed without truncated subtraction. -/
def CountClose (left right error : Nat) : Prop :=
  left ≤ right + error ∧ right ≤ left + error

theorem countClose_symm {left right error : Nat}
    (bound : CountClose left right error) : CountClose right left error :=
  ⟨bound.2, bound.1⟩

theorem countClose_triangle {left middle right errorLeft errorRight : Nat}
    (leftBound : CountClose left middle errorLeft)
    (rightBound : CountClose middle right errorRight) :
    CountClose left right (errorLeft + errorRight) := by
  rcases leftBound with ⟨leftUpper, middleUpper⟩
  rcases rightBound with ⟨middleLower, rightUpper⟩
  constructor <;> omega

/-- A count is bounded by its declared common sample size. -/
theorem acceptanceCount_le {View : Type uView}
    (distinguisher : View → Bool) (sampleSize : Nat)
    (samples : Fin sampleSize → View) :
    acceptanceCount distinguisher sampleSize samples ≤ sampleSize := by
  induction sampleSize with
  | zero => simp [acceptanceCount]
  | succ size inductionHypothesis =>
      have tailBound := inductionHypothesis (fun coin => samples coin.succ)
      simp only [acceptanceCount]
      split <;> omega

/-- If every coin yields a fixed observed bit, its count is exact. -/
theorem acceptanceCount_of_constant {View : Type uView}
    (distinguisher : View → Bool) (sampleSize : Nat)
    (samples : Fin sampleSize → View) (bit : Bool)
    (constant : ∀ coin, distinguisher (samples coin) = bit) :
    acceptanceCount distinguisher sampleSize samples =
      (if bit then sampleSize else 0) := by
  induction sampleSize with
  | zero => cases bit <;> rfl
  | succ size inductionHypothesis =>
      have tailConstant :
          ∀ coin : Fin size,
            distinguisher ((fun index : Fin size => samples index.succ) coin) = bit :=
        fun coin => constant coin.succ
      have tailCount :=
        inductionHypothesis (fun coin => samples coin.succ) tailConstant
      simp only [acceptanceCount]
      rw [constant 0, tailCount]
      cases bit <;> simp [Nat.add_comm]

variable {World : Type uWorld} {OwnInput : Type uOwn}
  {Leakage : Type uLeak} {View : Type uView}

/--
An explicit, conditional finite-game simulation obligation.

This quantifies over *every* Boolean distinguisher.  It is therefore a
statistical finite-model condition, stronger than merely claiming security
against efficient algorithms.  It must not be presented as a proved property
of HMAC, a particular 2PC library, or production executions.

The simulator receives `ownInput world` and `permittedLeakage world`, and no
other component of `world`.  Its sampling space has the same cardinality as
the real execution's sampling space.
-/
def SimulationBound (sampleSize : Nat)
    (ownInput : World → OwnInput)
    (permittedLeakage : World → Leakage)
    (realView : World → Fin sampleSize → View)
    (simulator : OwnInput → Leakage → Fin sampleSize → View)
    (error : Nat) : Prop :=
  ∀ world distinguisher,
    CountClose
      (acceptanceCount distinguisher sampleSize (realView world))
      (acceptanceCount distinguisher sampleSize
        (simulator (ownInput world) (permittedLeakage world)))
      error

/--
Conditional simulation composition: worlds with equal corrupt-party inputs
and equal permitted leakage have real distinguishing-count gap at most
`2 * error`.

There is no assertion here that `simulation` can be constructed for the
deployed system.  The equality hypotheses must cover all deliberately
revealed information, including the match result when it is disclosed.
-/
theorem simulation_implies_same_leakage_bound
    (sampleSize : Nat)
    (ownInput : World → OwnInput)
    (permittedLeakage : World → Leakage)
    (realView : World → Fin sampleSize → View)
    (simulator : OwnInput → Leakage → Fin sampleSize → View)
    (error : Nat)
    (simulation : SimulationBound sampleSize ownInput permittedLeakage
      realView simulator error)
    (leftWorld rightWorld : World)
    (sameOwnInput : ownInput leftWorld = ownInput rightWorld)
    (sameLeakage : permittedLeakage leftWorld = permittedLeakage rightWorld)
    (distinguisher : View → Bool) :
    CountClose
      (acceptanceCount distinguisher sampleSize (realView leftWorld))
      (acceptanceCount distinguisher sampleSize (realView rightWorld))
      (2 * error) := by
  have leftBound := simulation leftWorld distinguisher
  have rightBound := simulation rightWorld distinguisher
  have sameSimulator :
      acceptanceCount distinguisher sampleSize
          (simulator (ownInput leftWorld) (permittedLeakage leftWorld)) =
      acceptanceCount distinguisher sampleSize
          (simulator (ownInput rightWorld) (permittedLeakage rightWorld)) := by
    rw [sameOwnInput, sameLeakage]
  rcases leftBound with ⟨leftUpper, simulatorLeftUpper⟩
  rcases rightBound with ⟨rightUpper, simulatorRightUpper⟩
  constructor <;> omega

/-- Exact correctness of a publicly readable membership bit, for every coin. -/
def ExactMembershipOutput (sampleSize : Nat)
    (realView : World → Fin sampleSize → View)
    (membership : World → Bool)
    (readOutput : View → Bool) : Prop :=
  ∀ world coin, readOutput (realView world coin) = membership world

/--
An exact disclosed output is itself a distinguisher: it accepts on every
coin of a matching world and on no coin of a nonmatching world.
-/
theorem exact_membership_distinguishes
    (sampleSize : Nat)
    (realView : World → Fin sampleSize → View)
    (membership : World → Bool)
    (readOutput : View → Bool)
    (correct : ExactMembershipOutput sampleSize realView membership readOutput)
    (matchingWorld nonmatchingWorld : World)
    (isMatching : membership matchingWorld = true)
    (isNonmatching : membership nonmatchingWorld = false) :
    acceptanceCount readOutput sampleSize (realView matchingWorld) = sampleSize ∧
    acceptanceCount readOutput sampleSize (realView nonmatchingWorld) = 0 := by
  constructor
  · have constant : ∀ coin, readOutput (realView matchingWorld coin) = true := by
      intro coin
      rw [correct matchingWorld coin, isMatching]
    simpa using
      acceptanceCount_of_constant readOutput sampleSize
        (realView matchingWorld) true constant
  · have constant : ∀ coin, readOutput (realView nonmatchingWorld coin) = false := by
      intro coin
      rw [correct nonmatchingWorld coin, isNonmatching]
    simpa using
      acceptanceCount_of_constant readOutput sampleSize
        (realView nonmatchingWorld) false constant

/--
For a nonempty coin space, exact disclosed membership is incompatible with
identical acceptance counts for every distinguisher across opposite worlds.
This includes deterministic execution as the special case `sampleSize = 1`.
-/
theorem exact_membership_cannot_be_perfectly_hidden
    (sampleSize : Nat) (nonemptyCoins : 0 < sampleSize)
    (realView : World → Fin sampleSize → View)
    (membership : World → Bool)
    (readOutput : View → Bool)
    (correct : ExactMembershipOutput sampleSize realView membership readOutput)
    (matchingWorld nonmatchingWorld : World)
    (isMatching : membership matchingWorld = true)
    (isNonmatching : membership nonmatchingWorld = false) :
    ¬ (∀ distinguisher : View → Bool,
      acceptanceCount distinguisher sampleSize (realView matchingWorld) =
      acceptanceCount distinguisher sampleSize (realView nonmatchingWorld)) := by
  intro hidden
  have counts := exact_membership_distinguishes sampleSize realView membership
    readOutput correct matchingWorld nonmatchingWorld isMatching isNonmatching
  have equalCounts := hidden readOutput
  omega

/--
If a proposed simulator is denied the membership result (so its authorised
inputs remain equal across opposite-membership worlds), exact disclosed
membership forces a large error: `sampleSize ≤ 2 * error`.

For a nonempty uniform sample space this means a per-world simulation error
of at least one half is necessary.  To claim useful privacy, disclosed
membership must therefore be part of the permitted leakage, or the output
functionality itself must change.  This is a generic impossibility result;
no assumption about breaking HMAC is needed.
-/
theorem hiding_disclosed_membership_requires_large_error
    (sampleSize : Nat)
    (ownInput : World → OwnInput)
    (permittedLeakage : World → Leakage)
    (realView : World → Fin sampleSize → View)
    (simulator : OwnInput → Leakage → Fin sampleSize → View)
    (error : Nat)
    (simulation : SimulationBound sampleSize ownInput permittedLeakage
      realView simulator error)
    (membership : World → Bool)
    (readOutput : View → Bool)
    (correct : ExactMembershipOutput sampleSize realView membership readOutput)
    (matchingWorld nonmatchingWorld : World)
    (sameOwnInput : ownInput matchingWorld = ownInput nonmatchingWorld)
    (sameLeakage : permittedLeakage matchingWorld = permittedLeakage nonmatchingWorld)
    (isMatching : membership matchingWorld = true)
    (isNonmatching : membership nonmatchingWorld = false) :
    sampleSize ≤ 2 * error := by
  have bound := simulation_implies_same_leakage_bound sampleSize ownInput
    permittedLeakage realView simulator error simulation matchingWorld
    nonmatchingWorld sameOwnInput sameLeakage readOutput
  have counts := exact_membership_distinguishes sampleSize realView membership
    readOutput correct matchingWorld nonmatchingWorld isMatching isNonmatching
  rcases bound with ⟨matchingUpper, nonmatchingUpper⟩
  omega

end PatientMatching.Privacy
