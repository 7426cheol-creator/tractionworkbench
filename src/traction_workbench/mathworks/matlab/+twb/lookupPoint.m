function R = lookupPoint(M, T, C, id, iq)
%LOOKUPPOINT  Flux-map query: plane selection by magnet temperature T ([] = not stated), coverage, bilinear value.
[plane, reason] = twb.selectPlane(M.motor.flux, T, C.numerical_settings.temperature_match_tol_C);
R = struct('covered', false, 'psi_d_Wb', NaN, 'psi_q_Wb', NaN, 'reason', reason);
if isempty(plane)
    return
end
[psd, psq, ok] = twb.fluxMapLookup(plane.id_axis_A, plane.iq_axis_A, plane.psi_d_Wb, plane.psi_q_Wb, ...
                                   double(plane.cellValid), id, iq);
R.covered = ok;
if ok
    R.psi_d_Wb = psd;
    R.psi_q_Wb = psq;
    R.reason = '';
else
    R.reason = 'OUTSIDE_MODEL_DOMAIN';
end
end
