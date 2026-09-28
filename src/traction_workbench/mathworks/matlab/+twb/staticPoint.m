function y = staticPoint(P, id, iq)
%STATICPOINT  The steady-state fundamental dq equations at one (id, iq) for a resolved model P (twb.resolveModel).
%   y = [ok psi_d psi_q vd vq vcd vcq Te Tshaft Pac Pcu Pinv Pdc i2]   (SI; NaN = not defined by the model)
%       psi_d = psi_PM + Ld id, psi_q = Lq iq (constant dq)  |  masked bilinear map (flux map)
%       vd = Rs id - we psi_q,  vq = Rs iq + we psi_d,  vc = v + Rdrop i (declared inverter voltage error)
%       Te = 1.5 p (psi_d iq - psi_q id),  Tshaft = Te - tau_rot,  Pac = 1.5 (vd id + vq iq)
%       Pcu = 1.5 Rs |i|^2,  Pinv = a0 + a2 |i|^2 (quadratic surrogate),  Pdc = Pac + Pinv
%   The same function runs interpreted (twb.evaluatePoint) and inside the Simulink static evaluation harness.
%#codegen
if P.kind == 1
    psd = P.psi + P.Ld * id;
    psq = P.Lq * iq;
    if P.hasBox
        ok = id >= P.box(1) && id <= P.box(2) && iq >= P.box(3) && iq <= P.box(4);
    else
        ok = isfinite(psd) && isfinite(psq);
    end
else
    [psd, psq, ok] = twb.fluxMapLookup(P.idAxis, P.iqAxis, P.psiD, P.psiQ, P.cellValid, id, iq);
end
we = P.omega_e;
rs = P.Rs;
vd = rs * id - we * psq;
vq = rs * iq + we * psd;
vcd = vd + P.Rdrop * id;
vcq = vq + P.Rdrop * iq;
i2 = id * id + iq * iq;
tem = 1.5 * P.p * (psd * iq - psq * id);
pac = 1.5 * (vd * id + vq * iq);
pcu = 1.5 * rs * i2;
if P.hasRot
    tsh = tem - P.tauRot;
else
    tsh = NaN;
end
if P.hasQuadLoss
    pinv = P.a0 + P.a2 * i2;
else
    pinv = NaN;
end
pdc = pac + pinv;
y = [double(ok), psd, psq, vd, vq, vcd, vcq, tem, tsh, pac, pcu, pinv, pdc, i2];
end
