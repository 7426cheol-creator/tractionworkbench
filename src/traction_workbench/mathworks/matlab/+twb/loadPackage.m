function pkg = loadPackage(pkgDir)
%LOADPACKAGE  Read and check a twb-mathworks/1 transfer package: schema, file hashes, contract, models, cases.
%   The package is refused (error) when its schema is unknown, a listed file is missing or a file does not match
%   its recorded SHA-256 - a changed file is not the exported design.  Without a SHA-256 provider the hash check
%   is reported as NOT_RUN, never as passed.
man = twb.readJson(fullfile(pkgDir, 'manifest.json'));
if ~isfield(man, 'schema') || ~ischar(man.schema) || ~strcmp(man.schema, 'twb-mathworks/1')
    error('twb:schema', 'not a twb-mathworks/1 package (manifest schema %s): a later or unknown schema is not guessed', ...
          localSchema(man));
end
files = twb.asCell(man.files);
hashCheck = 'PASS';
problems = {};
for k = 1:numel(files)
    f = files{k};
    p = fullfile(pkgDir, strrep(f.path, '/', filesep));
    if exist(p, 'file') ~= 2
        problems{end + 1} = sprintf('missing file %s', f.path); %#ok<AGROW>
        continue
    end
    h = twb.sha256File(p);
    if isempty(h)
        hashCheck = 'NOT_RUN (no SHA-256 provider)';
    elseif ~strcmp(h, f.sha256)
        problems{end + 1} = sprintf('%s: sha256 %s is not the manifest''s %s', f.path, h(1:12), f.sha256(1:12)); %#ok<AGROW>
    end
end
if ~isempty(problems)
    error('twb:integrity', 'package integrity check failed:\n  %s', strjoin(problems, sprintf('\n  ')));
end
pkg.dir = pkgDir;
pkg.manifest = man;
pkg.files = files;
pkg.hashCheck = hashCheck;
pkg.contract = twb.readJson(fullfile(pkgDir, 'contract.json'));
twb.checkContract(pkg.contract);
pkg.models = struct();
for k = 1:numel(files)
    f = files{k};
    if strncmp(f.path, 'models/', 7)
        m = twb.readJson(fullfile(pkgDir, 'models', f.path(8:end)));
        twb.checkModel(m);
        pkg.models.(m.model_key) = m;
    end
end
sets = {'forward', 'flux_lookup', 'requirement_witness'};
pkg.cases = struct();
for k = 1:numel(sets)
    c = twb.readJson(fullfile(pkgDir, 'cases', [sets{k} '.json']));
    if ~strcmp(c.schema, 'twb-mathworks-cases/1')
        error('twb:schema', 'cases/%s.json: unknown schema %s', sets{k}, c.schema);
    end
    pkg.cases.(sets{k}) = twb.asCell(c.cases);
end
end

function s = localSchema(man)
s = '(none)';
if isfield(man, 'schema') && ischar(man.schema)
    s = man.schema;
end
end
