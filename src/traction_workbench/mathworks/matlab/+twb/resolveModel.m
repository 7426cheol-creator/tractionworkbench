function K = resolveModel(M, S, C)
%RESOLVEMODEL  One exported model at one scenario (the reference's DriveKernel): speeds, voltage budget, the
%   declared temperature laws (Rs, psi_PM) or the flux-map plane, the loss closures and their validity, and the
%   model-validity issues.  An issue never changes a number silently: the value is used as supplied, the issue is
%   recorded, and the evaluation becomes a diagnostic (not evidence).
%   S: scenario struct (speed_rpm, Vdc_V, winding_temp_C, magnet_temp_C, switching_frequency_Hz, limits).
st = C.numerical_settings;
tolC = st.temperature_match_tol_C;
K.model_key = M.model_key;
K.issues = struct('reason', {}, 'message', {});
K.notes = {};
p = M.motor.pole_pairs;
K.p = p;
K.speed_rpm = S.speed_rpm;
K.omega_m = 2 * pi * S.speed_rpm / 60;
K.omega_e = p * K.omega_m;
K.Vdc = S.Vdc_V;
v = M.inverter.voltage;
K.V_ceiling = S.Vdc_V / sqrt(3);
K.Vb = v.diagnostic_budget_scale * (1 - v.reserve_fraction) * S.Vdc_V / sqrt(3);
K.Rdrop = v.resistive_drop_ohm;
K.Imax = M.inverter.current_limit_A_peak;
K.domain = M.domain;
K.limits = S.limits;
fsw = field(S, 'switching_frequency_Hz');
if isempty(fsw) || fsw == 0
    fsw = M.inverter.switching_frequency_context_Hz;
end
K.fsw = fsw;

% winding resistance and its declared temperature law
rs0 = M.motor.Rs_ohm;
K.Rs = rs0;
t = field(S, 'winding_temp_C');
ref = M.motor.reference_winding_temp_C;
if isempty(t)
    K.notes{end + 1} = 'winding temperature not stated in scenario; Rs used as supplied';
elseif isempty(ref)
    K = issue(K, 'MISSING_INPUT', sprintf(['scenario winding temperature %g degC stated but the Rs reference ' ...
              'temperature is not declared'], t));
elseif abs(t - ref) > tolC
    dep = M.motor.rs_temperature;
    if isempty(dep) || t < dep.valid_C(1) || t > dep.valid_C(2)
        K = issue(K, 'OUTSIDE_MODEL_DOMAIN', sprintf(['Rs defined at %g degC; no validated temperature ' ...
                  'dependence covers %g degC'], ref, t));
    else
        rs = rs0 * (1 + dep.coeff_per_K * (t - ref));
        if ~isfinite(rs) || rs < 0
            K = issue(K, 'OUTSIDE_MODEL_DOMAIN', sprintf('the declared Rs temperature law gives Rs = %g ohm', rs));
        else
            K.Rs = rs;
        end
    end
end

% magnetic model
f = M.motor.flux;
K.plane = [];
K.psi = NaN;
if strcmp(f.kind, 'constant_dq')
    K.kind = 1;
    psi0 = f.psi_pm_Wb;
    K.psi = psi0;
    t = field(S, 'magnet_temp_C');
    ref = f.reference_magnet_temp_C;
    if isempty(t)
        K.notes{end + 1} = 'magnet temperature not stated in scenario; psi_PM used as supplied';
    elseif isempty(ref)
        K = issue(K, 'MISSING_INPUT', sprintf(['scenario magnet temperature %g degC stated but the psi_PM ' ...
                  'reference temperature is not declared'], t));
    elseif abs(t - ref) > tolC
        dep = f.psi_temperature;
        if isempty(dep) || t < dep.valid_C(1) || t > dep.valid_C(2)
            K = issue(K, 'OUTSIDE_MODEL_DOMAIN', sprintf(['psi_PM defined at %g degC; no validated ' ...
                      'temperature dependence covers %g degC'], ref, t));
        else
            psi = psi0 * (1 + dep.coeff_per_K * (t - ref));
            if ~isfinite(psi) || psi < 0
                K = issue(K, 'OUTSIDE_MODEL_DOMAIN', sprintf('the declared psi_PM law gives %g Wb', psi));
            else
                K.psi = psi;
            end
        end
    end
    K.Ld = f.Ld_H;
    K.Lq = f.Lq_H;
    K.evaluable = true;
else
    K.kind = 2;
    K.Ld = NaN;
    K.Lq = NaN;
    [K.plane, reason, message] = twb.selectPlane(f, field(S, 'magnet_temp_C'), tolC);
    if isempty(K.plane)
        K = issue(K, reason, message);
    end
    K.evaluable = ~isempty(K.plane);
end

% rotational loss-equivalent torque (null = not declared: shaft quantities undefined)
rot = M.motor.rotational_loss;
K.hasRot = ~isempty(rot);
if K.hasRot
    K.tauRot = rot.b_Nm_per_rad_s * K.omega_m + rot.c_Nm_per_rad2_s2 * K.omega_m * abs(K.omega_m);
    K.Prot = K.omega_m * K.tauRot;
else
    K.tauRot = NaN;
    K.Prot = NaN;
end

% inverter loss: the quadratic surrogate is native; a datasheet module model is not ported
L = M.inverter.loss;
K.lossKind = L.kind;
K.a0 = 0;
K.a2 = 0;
if strcmp(L.kind, 'quadratic_surrogate')
    K.a0 = L.offset_W;
    K.a2 = L.coeff_W_per_A2;
    vv = L.valid_Vdc_V;
    if ~isempty(vv) && ~(vv(1) <= K.Vdc && K.Vdc <= vv(2))
        K = issue(K, 'OUTSIDE_MODEL_DOMAIN', sprintf(['inverter loss surrogate validated for Vdc in [%g, %g] V, ' ...
                  'scenario Vdc=%g V'], vv(1), vv(2), K.Vdc));
    end
end
if abs(K.omega_m) <= st.speed_zero_tol_rad_s
    K.notes{end + 1} = 'standstill: RMS values are equivalent sinusoidal RMS, not individual phase RMS';
end

% the numeric parameter set of twb.staticPoint (all fields present for code generation)
P.kind = K.kind;
P.p = p;
P.omega_e = K.omega_e;
P.Rs = K.Rs;
P.Rdrop = K.Rdrop;
P.psi = 0;
P.Ld = 0;
P.Lq = 0;
P.hasBox = false;
P.box = zeros(1, 4);
P.idAxis = [0 1];
P.iqAxis = [0 1];
P.psiD = zeros(2, 2);
P.psiQ = zeros(2, 2);
P.cellValid = 0;
if K.kind == 1
    P.psi = K.psi;
    P.Ld = K.Ld;
    P.Lq = K.Lq;
    if ~isempty(f.validity)
        P.hasBox = true;
        P.box = [f.validity.id_A(1) f.validity.id_A(2) f.validity.iq_A(1) f.validity.iq_A(2)];
    end
elseif ~isempty(K.plane)
    P.idAxis = K.plane.id_axis_A;
    P.iqAxis = K.plane.iq_axis_A;
    P.psiD = K.plane.psi_d_Wb;
    P.psiQ = K.plane.psi_q_Wb;
    P.cellValid = double(K.plane.cellValid);
end
P.hasRot = K.hasRot;
P.tauRot = K.tauRot;
P.hasQuadLoss = strcmp(K.lossKind, 'quadratic_surrogate');
P.a0 = K.a0;
P.a2 = K.a2;
K.P = P;
K.torqueScale = torqueScale(K, f);
end

function x = field(S, name)
x = [];
if isfield(S, name)
    x = S.(name);
end
end

function K = issue(K, reason, message)
K.issues(end + 1) = struct('reason', reason, 'message', message);
end

function s = torqueScale(K, f)
% numerical scale of the torque residual tolerance (not a physical quantity)
if K.kind == 1
    s = max(1, 1.5 * K.p * (K.psi * K.Imax + abs(K.Ld - K.Lq) * K.Imax ^ 2 / 2));
    return
end
if ~isempty(K.plane)
    planes = {K.plane};
else
    planes = twb.asCell(f.planes);
end
s = 1;
for k = 1:numel(planes)
    P = planes{k};
    [D, Q] = ndgrid(P.id_axis_A, P.iq_axis_A);
    tem = 1.5 * K.p * (P.psi_d_Wb .* Q - P.psi_q_Wb .* D);
    v = logical(P.valid);
    s = max(s, max(abs(tem(v))));
end
end
