function info = buildArchitectureCandidate(pkg, profile, outDir, name)
%BUILDARCHITECTURECANDIDATE  A NEW System Composer model from architecture/candidates.json.
%   Components and the information signals (ports + interfaces with type and unit) are generated; physical
%   connections stay listed candidates (a physical port needs its domain and a behaviour - see the porting guide);
%   allocation / evidence links are not wires.  Target names come from the profile's explicit id_map, else the
%   default names; unmapped IDs are listed.  The model is created in outDir only and never overwrites a file.
%   Requires System Composer.  Written against the documented System Composer API.
A = twb.readJson(fullfile(pkg.dir, 'architecture', 'candidates.json'));
if nargin < 4 || isempty(name)
    name = 'twb_architecture_candidate';
end
if exist(outDir, 'dir') ~= 7
    mkdir(outDir);
end
file = fullfile(outDir, [name '.slx']);
if exist(file, 'file') == 2
    error('twb:architecture', '%s exists: the generator never overwrites a model', file);
end
[map, ~] = idMap(profile);
here = pwd;
cleanup = onCleanup(@() cd(here));
cd(outDir);
model = systemcomposer.createModel(name);
arch = model.Architecture;
comps = twb.asCell(A.components);
handles = struct();
unmapped = {};
for k = 1:numel(comps)
    [nm, mapped] = targetName(comps{k}.id, comps{k}.default_name, map);
    if ~mapped
        unmapped{end + 1} = comps{k}.id; %#ok<AGROW>
    end
    handles.(strrep(comps{k}.id, '.', '_')) = addComponent(arch, nm);
end
dict = model.InterfaceDictionary;
sigs = twb.asCell(A.signals);
for k = 1:numel(sigs)
    s = sigs{k};
    iname = strrep(s.id, '.', '_');
    iface = addInterface(dict, iname);
    addElement(iface, 'value', 'Type', 'double', 'Units', s.unit);
    src = strrep(s.from, '.', '_');
    dst = strrep(s.to, '.', '_');
    if isfield(handles, src) && isfield(handles, dst)
        po = addPort(handles.(src).Architecture, iname, 'out');
        pin = addPort(handles.(dst).Architecture, iname, 'in');
        setInterface(po, iface);
        setInterface(pin, iface);
        connect(po, pin);
    end
end
save(model);
info = struct('file', file, 'unmapped_ids', {unmapped}, ...
              'physical_connections_not_generated', {cellfun(@(x) x.id, twb.asCell(A.physical_connections), ...
                                                              'UniformOutput', false)});
end

function [map, prefix] = idMap(profile)
map = cell(0, 2);
prefix = '';
if ~isempty(profile) && isfield(profile, 'id_map')
    ids = twb.asCell(profile.id_map);
    for k = 1:numel(ids)
        if ischar(ids{k}.target) && ~isempty(ids{k}.target)
            map(end + 1, :) = {ids{k}.id, ids{k}.target}; %#ok<AGROW>
        end
    end
end
end

function [nm, mapped] = targetName(id, default, map)
nm = default;
mapped = false;
for k = 1:size(map, 1)
    if strcmp(map{k, 1}, id)
        nm = map{k, 2};
        mapped = true;
    end
end
end
