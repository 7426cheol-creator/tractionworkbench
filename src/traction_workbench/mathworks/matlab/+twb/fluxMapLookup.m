function [psd, psq, ok] = fluxMapLookup(xa, ya, PD, PQ, CV, x, y)
%FLUXMAPLOOKUP  Bilinear flux linkage inside valid cells only - no extrapolation, no clipping.
%   xa (id axis), ya (iq axis) strictly increasing; PD, PQ node values with PD(i, j) at (xa(i), ya(j));
%   CV(i, j) ~= 0 when cell i, j is valid.  A point on a cell edge is covered when any cell containing it is
%   valid (the lower cell is tried first, then the neighbours below in id, in iq and diagonally - the
%   reference's order).  Outside: ok = false and NaN values (UNKNOWN for the caller).
%   Written in the code-generation subset (scalar loops, no dynamic memory) so a Simulink MATLAB Function
%   block evaluates exactly the same arithmetic.
%#codegen
psd = NaN;
psq = NaN;
ok = false;
nd = numel(xa);
nq = numel(ya);
if ~(isfinite(x) && isfinite(y))
    return
end
if x < xa(1) || x > xa(nd) || y < ya(1) || y > ya(nq)
    return
end
i = locate(xa, x);
j = locate(ya, y);
good = CV(i, j) ~= 0;
if ~good
    onD = (i >= 2) && (x == xa(i));
    onQ = (j >= 2) && (y == ya(j));
    if onD && CV(i - 1, j) ~= 0
        i = i - 1;
        good = true;
    elseif onQ && CV(i, j - 1) ~= 0
        j = j - 1;
        good = true;
    elseif onD && onQ && CV(i - 1, j - 1) ~= 0
        i = i - 1;
        j = j - 1;
        good = true;
    end
end
if ~good
    return
end
t = (x - xa(i)) / (xa(i + 1) - xa(i));
u = (y - ya(j)) / (ya(j + 1) - ya(j));
psd = (1 - t) * (1 - u) * PD(i, j) + t * (1 - u) * PD(i + 1, j) + (1 - t) * u * PD(i, j + 1) + t * u * PD(i + 1, j + 1);
psq = (1 - t) * (1 - u) * PQ(i, j) + t * (1 - u) * PQ(i + 1, j) + (1 - t) * u * PQ(i, j + 1) + t * u * PQ(i + 1, j + 1);
ok = true;
end

function k = locate(a, v)
% the last node with a(k) <= v, kept inside [1, n-1] (the reference's searchsorted(side='right') - 1)
n = numel(a);
k = 1;
for m = 1:n
    if a(m) <= v
        k = m;
    end
end
if k > n - 1
    k = n - 1;
end
end
