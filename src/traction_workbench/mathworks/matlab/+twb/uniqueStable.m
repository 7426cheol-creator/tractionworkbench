function out = uniqueStable(c)
%UNIQUESTABLE  Unique strings of a cell array in first-occurrence order.
out = {};
for k = 1:numel(c)
    if ~any(strcmp(out, c{k}))
        out{end + 1} = c{k}; %#ok<AGROW>
    end
end
end
