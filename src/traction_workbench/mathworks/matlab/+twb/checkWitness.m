function W = checkWitness(M, S, C, witness, request)
%CHECKWITNESS  A requirement's witness point re-checked on the target against the ORIGINAL request.
%   FEASIBLE (claim_support) only when the point meets the requested shaft torque (or band) within the numerical
%   residual tolerance AND passes the evidence gate (model valid, covered, every constraint evaluated and not
%   violated, DC side defined with its binding limits declared, identities closed, passive).  A failed witness is
%   reported as UNKNOWN - one violating point is never an INFEASIBLE proof.
R = twb.evaluatePoint(M, S, C, witness.id_A, witness.iq_A, request);
W.witness_accepted = R.accepted;
W.reasons = R.gate_reasons;
W.torque_residual_Nm = R.torque_residual_Nm;
W.torque_tolerance_Nm = R.torque_tolerance_Nm;
W.Tshaft_Nm = R.Tshaft_Nm;
W.violated_groups = R.violated_groups;
if R.accepted
    W.claim_support = 'FEASIBLE';
else
    W.claim_support = 'UNKNOWN';
end
end
