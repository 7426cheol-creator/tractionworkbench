function res = compareDataItems(items, existing, profile)
%COMPAREDATAITEMS  Candidate data items (architecture/candidates.json) against existing dictionary entries.
%   items    - candidate items (id, default_name, value, unit, ...)
%   existing - entries of a company dictionary: struct array / cell with name, value, unit (scope optional)
%   profile  - company profile: id_map (list of id / target), naming.data_item_prefix
%   Status per item:
%     NEW                        no entry of that name (an adapter may create it; nothing is written here)
%     MAPPED_SAME                explicitly mapped, same unit and value
%     NAME_MATCH_ONLY            same name, unit and value, but NOT mapped: never assumed to be the same item
%     CONFLICT_UNIT / CONFLICT_VALUE   an entry of that name disagrees (reported, never overwritten)
%     CONFLICT_DUPLICATE_TARGET  two stable IDs map to one target name
items = twb.asCell(items);
existing = twb.asCell(existing);
[map, prefix] = profileMap(profile);
res = struct('id', {}, 'target_name', {}, 'mapped', {}, 'status', {}, 'detail', {});
targets = {};
for k = 1:numel(items)
    it = items{k};
    tgt = '';
    for m = 1:size(map, 1)
        if strcmp(map{m, 1}, it.id)
            tgt = map{m, 2};
        end
    end
    mapped = ~isempty(tgt);
    if ~mapped
        tgt = [prefix it.default_name];
    end
    r = struct('id', it.id, 'target_name', tgt, 'mapped', mapped, 'status', 'NEW', 'detail', '');
    if any(strcmp(targets, tgt))
        r.status = 'CONFLICT_DUPLICATE_TARGET';
        r.detail = 'another stable ID maps to the same target name';
    else
        e = [];
        for j = 1:numel(existing)
            if strcmp(existing{j}.name, tgt)
                e = existing{j};
            end
        end
        if ~isempty(e)
            if ~strcmp(unitOf(e), it.unit)
                r.status = 'CONFLICT_UNIT';
                r.detail = sprintf('existing unit "%s", package unit "%s"', unitOf(e), it.unit);
            elseif ~sameValue(e.value, it.value)
                r.status = 'CONFLICT_VALUE';
                r.detail = 'the existing value differs from the exported design value';
            elseif mapped
                r.status = 'MAPPED_SAME';
            else
                r.status = 'NAME_MATCH_ONLY';
                r.detail = 'same name, unit and value but no explicit mapping: map it in the profile to link it';
            end
        end
    end
    targets{end + 1} = tgt; %#ok<AGROW>
    res(end + 1) = r; %#ok<AGROW>
end
end

function [map, prefix] = profileMap(profile)
map = cell(0, 2);
prefix = '';
if isempty(profile)
    return
end
if isfield(profile, 'naming') && isfield(profile.naming, 'data_item_prefix') && ischar(profile.naming.data_item_prefix)
    prefix = profile.naming.data_item_prefix;
end
if isfield(profile, 'id_map')
    ids = twb.asCell(profile.id_map);
    for k = 1:numel(ids)
        if ischar(ids{k}.target) && ~isempty(ids{k}.target)
            map(end + 1, :) = {ids{k}.id, ids{k}.target}; %#ok<AGROW>
        end
    end
end
end

function u = unitOf(e)
u = '';
if isfield(e, 'unit') && ischar(e.unit)
    u = e.unit;
end
end

function ok = sameValue(a, b)
if ischar(a) || ischar(b) || iscell(a) || iscell(b)
    ok = isequal(a, b);
    return
end
a = double(a(:));
b = double(b(:));
ok = isequal(size(a), size(b)) && all(abs(a - b) <= 1e-12 * max(1, max(abs(a), abs(b))));
end
