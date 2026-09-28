function res = checkDictionary(pkg, slddFile, profile)
%CHECKDICTIONARY  Compare the package's data-item candidates with a company Simulink data dictionary (READ ONLY).
%   Lists the 'Design Data' entries (name, value, unit) and reports twb.compareDataItems statuses.  Nothing is
%   written to the dictionary.  Requires Simulink.
A = twb.readJson(fullfile(pkg.dir, 'architecture', 'candidates.json'));
dd = Simulink.data.dictionary.open(slddFile);
sec = getSection(dd, 'Design Data');
entries = find(sec);
existing = cell(1, numel(entries));
for k = 1:numel(entries)
    v = getValue(entries(k));
    e = struct('name', entries(k).Name, 'value', [], 'unit', '');
    if isa(v, 'Simulink.Parameter')
        e.value = v.Value;
        e.unit = v.Unit;
    elseif isnumeric(v) || islogical(v)
        e.value = v;
    end
    existing{k} = e;
end
close(dd);
res = twb.compareDataItems(A.data_items, existing, profile);
end
