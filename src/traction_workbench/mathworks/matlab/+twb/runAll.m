function report = runAll(pkgDir, varargin)
%RUNALL  The deterministic local entry point (no AI, no network):
%     preflight -> package check -> native evaluator parity -> Simulink static harness (when Simulink is
%     installed) -> results/parity_report.json
%   report = twb.runAll(pkgDir)                       all available stages
%   report = twb.runAll(pkgDir, 'Harness', 'off')     skip the Simulink harness
%   Every stage reports PASS / FAIL / ERROR / NOT_RUN on its own; a stage that did not run is NOT_RUN, never PASS.
%   The report names the package it consumed (semantic fingerprint and the SHA-256 of every file) so that the
%   Workbench links it only to that package (a report for another revision is not current evidence).
opt = struct('Harness', 'auto');
for k = 1:2:numel(varargin)
    opt.(varargin{k}) = varargin{k + 1};
end
env = twb.preflight();
resultsDir = fullfile(pkgDir, 'results');
if exist(resultsDir, 'dir') ~= 7
    mkdir(resultsDir);
end
twb.writeJson(fullfile(resultsDir, 'preflight.json'), env);
report.schema = 'twb-mathworks-report/1';
report.created = env.created;
report.runtime = env;
report.package = struct('semantic_fingerprint', '', 'consumed_files', {{}});
report.stages = struct();
report.cases = {};
report.summary = struct();
notRun = @(why) struct('status', 'NOT_RUN', 'detail', why);
try
    pkg = twb.loadPackage(pkgDir);
    report.stages.package_check = struct('status', 'PASS', 'detail', ['file hashes: ' pkg.hashCheck]);
catch err
    report.stages.package_check = struct('status', 'FAIL', 'detail', err.message);
    report.stages.native_evaluator = notRun('package check failed');
    report.stages.simulink_harness = notRun('package check failed');
    report.ok = false;
    twb.writeJson(fullfile(resultsDir, 'parity_report.json'), report);
    fprintf('twb: package check FAILED - %s\n', err.message);
    return
end
report.package.semantic_fingerprint = pkg.manifest.semantic_fingerprint;
consumed = cell(1, numel(pkg.files));
for k = 1:numel(pkg.files)
    consumed{k} = struct('path', pkg.files{k}.path, 'sha256', pkg.files{k}.sha256);
end
report.package.consumed_files = consumed;
parity = twb.runParity(pkg);
report.stages.native_evaluator = struct('status', parity.status, 'detail', sprintf( ...
    '%s %s: %d PASS, %d FAIL, %d ERROR, %d NOT_SUPPORTED of %d cases', env.product, env.version, ...
    parity.summary.PASS, parity.summary.FAIL, parity.summary.ERROR, parity.summary.NOT_SUPPORTED, ...
    parity.summary.total));
report.cases = parity.cases;
report.summary = parity.summary;
try
    report.stages.self_checks = twb.selfChecks(pkg);
catch err
    report.stages.self_checks = struct('status', 'ERROR', 'detail', err.message);
end
if strcmp(opt.Harness, 'off')
    report.stages.simulink_harness = notRun('switched off by the caller');
elseif ~env.toolboxes.simulink.available
    report.stages.simulink_harness = notRun(env.toolboxes.simulink.reason);
else
    try
        report.stages.simulink_harness = twb.runHarness(pkg, fullfile(resultsDir, 'harness'));
    catch err
        report.stages.simulink_harness = struct('status', 'ERROR', 'detail', err.message);
    end
end
report.stages.system_composer = notRun(['explicit step: twb.buildArchitectureCandidate(pkg, profile, outDir) ' ...
    'creates a NEW candidate model; the company architecture is never modified']);
report.stages.dictionary_conflicts = notRun(['explicit step: twb.checkDictionary(pkg, slddFile, profile) reads ' ...
    'a company dictionary (read only)']);
report.stages.physical_validation = struct('status', 'NOT_CLAIMED', 'detail', pkg.contract.verification_level);
report.ok = strcmp(report.stages.package_check.status, 'PASS') && strcmp(parity.status, 'PASS') && ...
            strcmp(report.stages.self_checks.status, 'PASS');
twb.writeJson(fullfile(resultsDir, 'parity_report.json'), report);
fprintf(['twb: %s %s | package %s | native evaluator %s (%d PASS, %d FAIL, %d ERROR, %d NOT_SUPPORTED) | ' ...
         'guards %s | Simulink harness %s\n'], env.product, env.version, report.stages.package_check.status, ...
        parity.status, parity.summary.PASS, parity.summary.FAIL, parity.summary.ERROR, ...
        parity.summary.NOT_SUPPORTED, report.stages.self_checks.status, report.stages.simulink_harness.status);
for k = 1:numel(parity.cases)
    c = parity.cases{k};
    if any(strcmp(c.status, {'FAIL', 'ERROR'}))
        fprintf('  %s %s: %s\n', c.status, c.case_id, strjoin(c.failures, '; '));
    end
end
end
