/* Traction Workbench UI — vanilla JS, SVG charts, no external dependencies. */
(() => {
  'use strict';
  const STATIC = window.TWB_STATIC || null;
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const store = {
    get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : v; } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private mode */ } },
  };
  const state = { lang: store.get('twb.lang', (navigator.language || 'ko').startsWith('ko') ? 'ko' : 'en'), info: null,
    capVdc: [450, 600], curveCache: {}, lastRecord: null, lastPayload: null };

  // ------------------------------------------------------------------ i18n
  const I18N = {
    ko: {
      'tab.decision': '요구 판정', 'tab.capability': '성능 곡선', 'tab.design': '설계·병목', 'tab.safety': '안전 스크리닝',
      'tab.thermal': '열·지속시간', 'tab.vv': '검증(V&V)',
      'dec.title': '요구사항 판정', 'dec.lead': '고객 요구(축 토크·속도·DC 전압)를 입력하면 해/불가능 근거/판단 불가 사유를 추적 가능한 의사결정 기록으로 반환합니다.',
      'dec.empty': '<b>왼쪽에서 예시를 고르거나 요구를 입력하고 평가를 실행하세요.</b><br>결과는 판정 → 조건 → id/iq → 전력 수지 → 제약 → 결론 순서로 추적됩니다.',
      'f.id': '요구 ID', 'f.text': '원문 (그대로 보존)', 'f.torque': '축 토크 (+구동 / −제동)', 'f.speed': '기계 속도',
      'f.vpoint': 'Vdc 한 점', 'f.vrange': 'Vdc 전 구간', 'f.vdc': 'DC 단자 전압', 'f.vdc_hi': 'Vdc 상한',
      'f.duration': '지속시간 (선택)', 'f.coolant': '냉각수 온도 (선택)', 'f.advanced': 'DC 한계·추가 분석',
      'f.pdis': '방전 전력 한계', 'f.pchg': '충전 전력 한계', 'f.idis': '방전 평균전류 한계', 'f.ichg': '충전 평균전류 한계',
      'f.an_dom': '병목 분석(제약 완화 재계산)', 'f.an_size': 'Vdc 역설계 (400–800 V)', 'f.run': '평가 실행',
      'f.note': '지속시간을 비워두면 정적 항목으로만 해석하며 연속(continuous)으로 간주하지 않습니다.',
      'cap.title': '토크–속도 성능 곡선', 'cap.vdc': 'Vdc', 'cap.run': '계산', 'cap.lead': '실선: 최소전류 정책(DC 한계 포함) 경계 · 점선: 전기적 한계(전압·전류·운전영역, DC 제외). 두 곡선의 차이가 DC 경계가 막는 영역입니다.',
      'cap.note': '속도 표본점 사이의 곡선은 보간 표시일 뿐 검증된 경계가 아닙니다.',
      'des.sizing': '1-파라미터 역설계', 'des.sizing_lead': '한 번에 하나의 파라미터만 탐색 범위 안에서 바꾸며 결합 문제 전체를 다시 풉니다. 범위 밖은 외삽하지 않습니다.',
      'des.param': '파라미터', 'des.lo': '하한', 'des.hi': '상한', 'des.run_sizing': '탐색',
      'des.dom': '병목(dominance) 분석', 'des.dom_lead': '각 제약을 1% 완화하고 capability를 다시 계산합니다. "현재 active"와 "capability를 제한"은 다른 개념입니다.',
      'des.dir': '방향', 'des.motoring': '구동 (최대)', 'des.braking': '제동 (최소)', 'des.run_dom': '분석',
      'saf.scope_t': '스크리닝 범위:', 'saf.scope': '정상상태·에너지 수지 기반 축약 모델입니다. 과도 파형, 소자 SOA, 기능안전 승인을 대신하지 않으며 과도 항목은 UNKNOWN으로 남깁니다.',
      'saf.dclink': 'DC-link', 'saf.safestate': 'ASC / Freewheel',
      'ftti.title': '고장 반응 시간 체인 vs FTTI', 'ftti.run': '분석', 'ftti.add': '구간 추가',
      'ftti.lead': '구간별 min/nom/max 지연과 담당자를 입력합니다. 같은 구간을 두 담당자가 중복 예산화하면 자동 검출합니다. 주기 태스크는 한 주기의 샘플링 지연을 더합니다.',
      'dc.dis_t': '능동 방전 (저항)', 'dc.ov_t': '회생 중 배터리 차단 과전압', 'dc.t': '목표 시간', 'dc.opt': '(선택)', 'dc.spin': '모터 회전 속도',
      'dc.regen_t': '회생 토크', 'dc.react': '반응 시간', 'dc.const': '일정 전력', 'dc.ramp': '선형 감소', 'dc.run': '계산',
      'ss.title': '안전 반응 후보 스크리닝 (정상상태)', 'ss.hv': 'HV 상태', 'ss.conn': '배터리 연결', 'ss.disc': '배터리 차단',
      'ss.dev': '소자 전압 정격', 'ss.rule': '프로젝트 규칙: Vdc 미만이면 Freewheel', 'ss.run': '스크리닝',
      'th.scope_t': '예시 열모델(미검증):', 'th.scope': '열 네트워크가 검증되지 않았으면 지속시간 판정은 UNKNOWN으로 유지되고 수치는 스크리닝 추정치로만 표시됩니다.',
      'th.title': '열 → 토크 가용성', 'th.model': '열 네트워크 (JSON, Foster R/τ, 손실 배분)', 'th.run': '계산',
      'vv.title': '골든 픽스처 대비 검증', 'vv.run': '다시 실행', 'vv.lead': '불변 참조 JSON과 현재 계산 결과를 비교합니다. 합성 fixture 검증이며 실기·공급사 데이터 검증이 아닙니다.',
      'vv.limits_t': '알려진 한계',
      'foot': '합성 참조 데이터 · 하드웨어 미검증 · 정상상태 기본파 모델 · 표시값은 반올림, 판정은 전체 정밀도',
      'verdict.FEASIBLE': 'PASS', 'verdict.INFEASIBLE': 'FAIL', 'verdict.UNKNOWN': 'UNKNOWN',
      'st.FEASIBLE': '가능', 'st.INFEASIBLE': '불가능', 'st.UNKNOWN': '판단 불가',
      'hl.pass': '명시된 범위에서 정적으로 달성 가능', 'hl.fail': '입증된 위반이 있어 불가능', 'hl.unknown': '근거 부족으로 판정 불가',
      'trace.req': '요구', 'trace.cond': '조건', 'trace.op': 'id/iq', 'trace.pow': '전력 수지', 'trace.cons': '제약', 'trace.conc': '결론',
      'sec.claims': '판정 항목 (claim)', 'sec.op': '운전점 (최소전류 정책)', 'sec.power': '부호 있는 전력 수지', 'sec.cons': '제약 여유 (고유 단위)',
      'sec.charts': 'T–n 영역과 id–iq 제약 지도', 'sec.conc': '제한 요인 · 다음 조치', 'sec.analyses': '추가 분석', 'sec.record': '의사결정 기록',
      'k.id': 'id', 'k.iq': 'iq', 'k.ipk': '|i| 피크', 'k.irms': '상 RMS', 'k.te': '전자기 토크', 'k.tsh': '축 토크', 'k.margin': '토크 여유',
      'k.cap': '정책 capability', 'k.vm': '잔여 전압 여유', 'k.pdc': 'P_dc', 'k.idc': 'I_dc 평균', 'k.eta': '효율',
      'lim.t': '제한 요인', 'next.t': '다음 조치', 'noteval.t': '이 기록이 평가하지 않은 것',
      'rec.download_json': 'JSON 다운로드', 'rec.download_md': 'Markdown 다운로드', 'rec.copy': '해시 복사',
      'cond.t': '검사한 조건', 'evidence': '근거', 'reasons': '사유',
      'budget.ceiling': '하드웨어 한계 Vdc/√3', 'budget.reserve': '예약분', 'budget.demand': '명령 전압',
      'size.min': '최소 가능값', 'size.none': '탐색 범위 안에 가능한 값 없음', 'size.ranges': '가능 구간',
      'dom.gain': '1% 완화 시 capability 증가', 'dom.joint': '공동 병목', 'dom.active': '현재 active',
      'static.note': '정적 스냅샷: 미리 계산된 예시만 표시합니다. 자유 입력 계산은 로컬에서 <code>twb serve</code>로 실행하세요.',
      'err': '오류', 'loading': '계산 중…',
    },
    en: {
      'tab.decision': 'Decision', 'tab.capability': 'Capability', 'tab.design': 'Design & bottlenecks', 'tab.safety': 'Safety screening',
      'tab.thermal': 'Thermal & duration', 'tab.vv': 'Verification',
      'dec.title': 'Requirement decision', 'dec.lead': 'Enter a customer requirement (shaft torque, speed, DC voltage) and get a solution, an infeasibility proof or the reason it cannot be decided — as a traceable decision record.',
      'dec.empty': '<b>Pick an example or enter a requirement on the left and run it.</b><br>Results trace verdict → conditions → id/iq → power balance → constraints → conclusion.',
      'f.id': 'Requirement ID', 'f.text': 'Original text (kept verbatim)', 'f.torque': 'Shaft torque (+motoring / −braking)', 'f.speed': 'Mechanical speed',
      'f.vpoint': 'Single Vdc', 'f.vrange': 'Whole Vdc range', 'f.vdc': 'DC terminal voltage', 'f.vdc_hi': 'Vdc upper',
      'f.duration': 'Duration (optional)', 'f.coolant': 'Coolant temp (optional)', 'f.advanced': 'DC limits & extra analyses',
      'f.pdis': 'Discharge power limit', 'f.pchg': 'Charge power limit', 'f.idis': 'Discharge avg. current', 'f.ichg': 'Charge avg. current',
      'f.an_dom': 'Bottleneck analysis (relax & re-solve)', 'f.an_size': 'Vdc sizing (400–800 V)', 'f.run': 'Evaluate',
      'f.note': 'An empty duration means a static item only; it is never read as "continuous".',
      'cap.title': 'Torque–speed capability', 'cap.vdc': 'Vdc', 'cap.run': 'Compute', 'cap.lead': 'Solid: minimum-current policy boundary incl. DC limits · Dashed: electrical limit (voltage, current, domain; no DC). The gap between them is what the DC boundary blocks.',
      'cap.note': 'Lines between speed samples are drawn for reading only; the boundary is established at the samples.',
      'des.sizing': 'One-parameter sizing', 'des.sizing_lead': 'Changes one parameter inside the search range and re-solves the whole coupled problem. No extrapolation outside the range.',
      'des.param': 'Parameter', 'des.lo': 'Low', 'des.hi': 'High', 'des.run_sizing': 'Search',
      'des.dom': 'Dominance analysis', 'des.dom_lead': 'Relaxes each constraint by 1% and recomputes the capability. "Active now" and "limits the capability" are different things.',
      'des.dir': 'Direction', 'des.motoring': 'Motoring (max)', 'des.braking': 'Braking (min)', 'des.run_dom': 'Analyse',
      'saf.scope_t': 'Screening scope:', 'saf.scope': 'Reduced-order steady-state / energy-balance models. Not transient waveforms, device SOA or functional-safety approval; transient aspects stay UNKNOWN.',
      'saf.dclink': 'DC link', 'saf.safestate': 'ASC / Freewheel',
      'ftti.title': 'Fault-reaction timing chain vs FTTI', 'ftti.run': 'Analyse', 'ftti.add': 'Add item',
      'ftti.lead': 'Enter min/nom/max latency and owner per interval. Two owners budgeting the same interval are detected automatically. Periodic tasks add one sampling period.',
      'dc.dis_t': 'Active discharge (resistor)', 'dc.ov_t': 'Battery disconnect during regen', 'dc.t': 'Target time', 'dc.opt': '(optional)', 'dc.spin': 'Motor speed',
      'dc.regen_t': 'Regen torque', 'dc.react': 'Reaction time', 'dc.const': 'Constant power', 'dc.ramp': 'Linear ramp-down', 'dc.run': 'Compute',
      'ss.title': 'Safe-reaction candidate screening (steady state)', 'ss.hv': 'HV state', 'ss.conn': 'Battery connected', 'ss.disc': 'Battery disconnected',
      'ss.dev': 'Device voltage rating', 'ss.rule': 'Project rule: freewheel below Vdc', 'ss.run': 'Screen',
      'th.scope_t': 'Example thermal model (unvalidated):', 'th.scope': 'Without a validated thermal network the duration claim stays UNKNOWN and the numbers are screening estimates only.',
      'th.title': 'Thermal → torque availability', 'th.model': 'Thermal network (JSON: Foster R/τ, loss shares)', 'th.run': 'Compute',
      'vv.title': 'Verification against golden fixtures', 'vv.run': 'Run again', 'vv.lead': 'Compares current results with the immutable reference JSON. Synthetic-fixture verification, not hardware or supplier-data validation.',
      'vv.limits_t': 'Known limitations',
      'foot': 'Synthetic reference data · not hardware-validated · steady-state fundamental model · displayed values rounded, decisions at full precision',
      'verdict.FEASIBLE': 'PASS', 'verdict.INFEASIBLE': 'FAIL', 'verdict.UNKNOWN': 'UNKNOWN',
      'st.FEASIBLE': 'feasible', 'st.INFEASIBLE': 'infeasible', 'st.UNKNOWN': 'unknown',
      'hl.pass': 'Statically achievable in the stated scope', 'hl.fail': 'Infeasible: a violation is proven', 'hl.unknown': 'Cannot be decided with the available evidence',
      'trace.req': 'Requirement', 'trace.cond': 'Conditions', 'trace.op': 'id/iq', 'trace.pow': 'Power balance', 'trace.cons': 'Constraints', 'trace.conc': 'Conclusion',
      'sec.claims': 'Claims', 'sec.op': 'Operating point (minimum-current policy)', 'sec.power': 'Signed power balance', 'sec.cons': 'Constraint margins (native units)',
      'sec.charts': 'T–n region and id–iq constraint map', 'sec.conc': 'Limiting factors · next actions', 'sec.analyses': 'Additional analyses', 'sec.record': 'Decision record',
      'k.id': 'id', 'k.iq': 'iq', 'k.ipk': '|i| peak', 'k.irms': 'phase RMS', 'k.te': 'EM torque', 'k.tsh': 'Shaft torque', 'k.margin': 'Torque margin',
      'k.cap': 'Policy capability', 'k.vm': 'Voltage margin', 'k.pdc': 'P_dc', 'k.idc': 'I_dc avg.', 'k.eta': 'Efficiency',
      'lim.t': 'Limiting factors', 'next.t': 'Next actions', 'noteval.t': 'Not evaluated by this record',
      'rec.download_json': 'Download JSON', 'rec.download_md': 'Download Markdown', 'rec.copy': 'Copy hash',
      'cond.t': 'Examined conditions', 'evidence': 'Evidence', 'reasons': 'Reasons',
      'budget.ceiling': 'Hardware ceiling Vdc/√3', 'budget.reserve': 'reserve', 'budget.demand': 'command voltage',
      'size.min': 'Minimum feasible value', 'size.none': 'No feasible value inside the searched range', 'size.ranges': 'Feasible ranges',
      'dom.gain': 'Capability gain for +1% relaxation', 'dom.joint': 'Joint bottleneck', 'dom.active': 'active now',
      'static.note': 'Static snapshot: only precomputed examples are shown. Run <code>twb serve</code> locally for free-form calculations.',
      'err': 'Error', 'loading': 'Computing…',
    },
  };
  const REASONS = {
    ko: { MISSING_INPUT: '입력 누락', OUTSIDE_MODEL_DOMAIN: '모델 범위 밖', OUTSIDE_ALLOWED_OPERATING_DOMAIN: '허용 운전영역 밖',
      UNVALIDATED_DURATION: '지속시간 근거 없음', UNCERTAINTY_OVERLAP: '불확실성 중첩', NUMERICAL_UNRESOLVED: '수치 미해결',
      INVALID_INPUT: '잘못된 입력', POLICY_LIMITATION: '정책 미정의', SAMPLED_COVERAGE: '표본 검사만', BOUNDARY_WITHIN_TOLERANCE: '경계(허용오차 내)',
      OUT_OF_SCOPE: '범위 밖', CONSTRAINT_VIOLATION: '제약 위반', NECESSARY_CONDITION_VIOLATED: '필요조건 위반' },
    en: {},
  };
  const CLAIM_NAMES = {
    ko: { electrical_existence: '전기적 해 존재', policy_static: '정책 정적 달성 (DC 포함)', dc_source: 'DC 소스 한계',
      physical_existence_with_dc: '임의 제어로 가능? (진단)', duration: '지속시간', policy_static_in_band: '허용 밴드 내 달성' },
    en: { electrical_existence: 'Electrical solution exists', policy_static: 'Policy achieves it (incl. DC)', dc_source: 'DC source limits',
      physical_existence_with_dc: 'Any control could? (diagnostic)', duration: 'Duration', policy_static_in_band: 'Achieved within band' },
  };
  const t = (k) => (I18N[state.lang] && I18N[state.lang][k]) || I18N.en[k] || k;
  const reasonText = (r) => (REASONS[state.lang] && REASONS[state.lang][r]) || r;
  function applyI18n() {
    document.documentElement.lang = state.lang;
    $$('[data-i18n]').forEach((el) => { el.textContent = t(el.dataset.i18n); });
    $$('[data-i18n-html]').forEach((el) => { el.innerHTML = t(el.dataset.i18nHtml); });
    $('#lang').textContent = state.lang === 'ko' ? 'EN' : '한국어';
    if (STATIC) { const n = $('#static-note'); n.hidden = false; n.innerHTML = t('static.note'); }
  }

  // ------------------------------------------------------------------ helpers
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  function fmt(x, d = 4) {
    if (x === null || x === undefined || x === '') return '—';
    if (typeof x === 'string') return x === 'Infinity' ? '∞' : x;
    if (!Number.isFinite(x)) return x > 0 ? '∞' : (x < 0 ? '−∞' : 'NaN');
    if (x === 0) return '0';
    const a = Math.abs(x);
    if (a >= 1e5) return (x / 1000).toLocaleString(undefined, { maximumFractionDigits: 1 }) + 'k';
    if (a < 1e-3) return x.toExponential(2);
    const s = Number(x.toPrecision(d));
    return s.toLocaleString(undefined, { maximumFractionDigits: 6 });
  }
  const fmtU = (x, u, d) => `${fmt(x, d)}${x === null || x === undefined ? '' : ' ' + u}`;
  const statusChip = (s) => `<span class="status ${s}"><span class="ic">${s === 'FEASIBLE' ? '✓' : s === 'INFEASIBLE' ? '✕' : '?'}</span>${t('st.' + s)}</span>`;
  const pill = (ok) => ok === null || ok === undefined ? '<span class="pill unk">?</span>' : ok ? '<span class="pill ok">PASS</span>' : '<span class="pill bad">FAIL</span>';
  function toast(msg, err) {
    const el = $('#toast'); el.textContent = msg; el.className = 'toast' + (err ? ' err' : ''); el.hidden = false;
    clearTimeout(toast._t); toast._t = setTimeout(() => { el.hidden = true; }, err ? 7000 : 2500);
  }
  function download(name, text, type) {
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([text], { type })); a.download = name; a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  }
  function busy(el, on) { if (el) el.classList.toggle('loading', !!on); }

  // ------------------------------------------------------------------ API
  async function api(name, body) {
    if (STATIC) return staticApi(name, body);
    const opt = body === undefined ? { method: 'GET' } : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
    const res = await fetch('/api/' + name, opt);
    let data;
    try { data = await res.json(); } catch (e) { throw new Error(res.statusText); }
    if (!res.ok) { const e = new Error(data.error || res.statusText); e.data = data; throw e; }
    return data;
  }
  function staticApi(name, body) {
    const S = STATIC;
    const miss = () => Promise.reject(new Error(state.lang === 'ko' ? '정적 스냅샷에는 이 입력의 결과가 없습니다 (예시만 제공).' : 'Not in the static snapshot (examples only).'));
    if (name === 'info') return Promise.resolve(S.info);
    if (name === 'acceptance') return Promise.resolve(S.acceptance);
    if (name === 'evaluate') return body.preset && S.evaluate[body.preset] ? Promise.resolve(S.evaluate[body.preset]) : miss();
    if (name === 'curve') return S.curves[String(body.Vdc_V)] ? Promise.resolve(S.curves[String(body.Vdc_V)]) : miss();
    if (name === 'map') { const k = `${body.speed_rpm}|${body.Vdc_V}|${body.torque_Nm}`; return S.maps[k] ? Promise.resolve(S.maps[k]) : miss(); }
    const fixed = S.fixed || {};
    if (fixed[name]) return Promise.resolve(fixed[name]);
    return miss();
  }

  // ------------------------------------------------------------------ SVG charts
  const NS = 'http://www.w3.org/2000/svg';
  function el(tag, attrs = {}, parent) {
    const e = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) if (v !== undefined && v !== null) e.setAttribute(k, v);
    if (parent) parent.appendChild(e);
    return e;
  }
  function niceTicks(min, max, n = 6) {
    if (!(max > min)) { max = min + 1; }
    const span = max - min; const step0 = span / n; const mag = Math.pow(10, Math.floor(Math.log10(step0)));
    const err = step0 / mag; const step = (err >= 7.5 ? 10 : err >= 3.5 ? 5 : err >= 1.5 ? 2 : 1) * mag;
    const out = []; for (let v = Math.ceil(min / step) * step; v <= max + step * 1e-9; v += step) out.push(Math.abs(v) < step * 1e-9 ? 0 : v);
    return out;
  }
  const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

  const charts = new Map();
  function register(container, draw) { charts.set(container, draw); }
  let resizeTimer = null;
  window.addEventListener('resize', () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => { for (const [c, draw] of charts) { if (document.body.contains(c)) { if (c.offsetParent !== null) draw(); } else charts.delete(c); } }, 160);
  });
  const widthOf = (container, fallback = 720) => Math.max(300, Math.round(container.clientWidth || fallback));

  function lineChart(container, cfg) {
    register(container, () => lineChart(container, cfg));
    container.innerHTML = '';
    const W = widthOf(container, cfg.width || 720), H = cfg.height || 360, m = { l: 58, r: 16, t: 14, b: 44 };
    const svg = el('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': cfg.aria || cfg.yLabel || 'chart' });
    container.appendChild(svg);
    const all = cfg.series.flatMap((s) => s.points.filter((p) => p[1] !== null && Number.isFinite(p[1])));
    const extra = (cfg.markers || []).map((p) => [p.x, p.y]).concat((cfg.hlines || []).map((h) => [all.length ? all[0][0] : 0, h.y]));
    const pts = all.concat(extra);
    const lx = cfg.xLog;
    const fx = (v) => (lx ? Math.log10(Math.max(v, 1e-9)) : v);
    let xmin = cfg.xMin ?? Math.min(...pts.map((p) => fx(p[0]))), xmax = cfg.xMax ?? Math.max(...pts.map((p) => fx(p[0])));
    let ymin = cfg.yMin ?? Math.min(0, ...pts.map((p) => p[1])), ymax = cfg.yMax ?? Math.max(...pts.map((p) => p[1]));
    if (!(xmax > xmin)) xmax = xmin + 1;
    const pad = (ymax - ymin) * 0.06 || 1; ymin -= cfg.yMin !== undefined ? 0 : pad; ymax += pad;
    const X = (v) => m.l + (fx(v) - xmin) / (xmax - xmin) * (W - m.l - m.r);
    const Y = (v) => H - m.b - (v - ymin) / (ymax - ymin) * (H - m.t - m.b);
    const g = el('g', { class: 'axis' }, svg);
    const dense = W >= 560;
    for (const v of niceTicks(ymin, ymax, dense ? 6 : 5)) {
      el('line', { x1: m.l, x2: W - m.r, y1: Y(v), y2: Y(v), class: 'gridline' }, g);
      el('text', { x: m.l - 6, y: Y(v) + 4, 'text-anchor': 'end' }, g).textContent = fmt(v, 4);
    }
    const xt = lx ? (cfg.xTicks || []).filter((v, i, a) => dense || i % 2 === 0 || i === a.length - 1) : niceTicks(xmin, xmax, dense ? 7 : 4);
    for (const v of xt) {
      const xv = lx ? v : v;
      el('line', { x1: X(xv), x2: X(xv), y1: m.t, y2: H - m.b, class: 'gridline' }, g);
      el('text', { x: X(xv), y: H - m.b + 16, 'text-anchor': 'middle' }, g).textContent = cfg.xFmt ? cfg.xFmt(v) : fmt(v, 4);
    }
    if (ymin < 0 && ymax > 0) el('line', { x1: m.l, x2: W - m.r, y1: Y(0), y2: Y(0), stroke: cssVar('--muted'), 'stroke-width': 1 }, g);
    el('text', { x: (m.l + W - m.r) / 2, y: H - 6, 'text-anchor': 'middle', class: 'title' }, svg).textContent = cfg.xLabel || '';
    const yl = el('text', { x: 14, y: (m.t + H - m.b) / 2, 'text-anchor': 'middle', class: 'title', transform: `rotate(-90 14 ${(m.t + H - m.b) / 2})` }, svg);
    yl.textContent = cfg.yLabel || '';
    for (const b of cfg.bands || []) el('rect', { x: X(b.x0), width: Math.max(1, X(b.x1) - X(b.x0)), y: m.t, height: H - m.t - m.b, fill: b.color, opacity: b.opacity ?? 0.12 }, svg);
    for (const h of cfg.hlines || []) {
      el('line', { x1: m.l, x2: W - m.r, y1: Y(h.y), y2: Y(h.y), stroke: h.color, 'stroke-dasharray': '6 4', 'stroke-width': 1.5 }, svg);
      if (h.label) el('text', { x: W - m.r - 4, y: Y(h.y) - 5, 'text-anchor': 'end', fill: h.color }, svg).textContent = h.label;
    }
    for (const v of cfg.vlines || []) {
      el('line', { x1: X(v.x), x2: X(v.x), y1: m.t, y2: H - m.b, stroke: v.color, 'stroke-dasharray': '6 4', 'stroke-width': 1.5 }, svg);
      if (v.label) el('text', { x: X(v.x) + 4, y: m.t + 12, fill: v.color }, svg).textContent = v.label;
    }
    for (const s of cfg.series) {
      let d = ''; let pen = false;
      for (const p of s.points) {
        if (p[1] === null || !Number.isFinite(p[1])) { pen = false; continue; }
        d += `${pen ? 'L' : 'M'}${X(p[0]).toFixed(1)},${Y(p[1]).toFixed(1)}`; pen = true;
      }
      if (s.fill) el('path', { d: d + `L${X(s.points[s.points.length - 1][0])},${Y(0)}L${X(s.points[0][0])},${Y(0)}Z`, fill: s.color, opacity: 0.08 }, svg);
      el('path', { d, fill: 'none', stroke: s.color, 'stroke-width': s.width || 2, 'stroke-dasharray': s.dash ? '6 4' : null, 'stroke-linejoin': 'round' }, svg);
      if (s.dots) for (const p of s.points) if (p[1] !== null && Number.isFinite(p[1])) el('circle', { cx: X(p[0]), cy: Y(p[1]), r: 2.5, fill: s.color }, svg);
    }
    for (const mk of cfg.markers || []) {
      const c = el('circle', { cx: X(mk.x), cy: Y(mk.y), r: mk.r || 6, fill: mk.hollow ? cssVar('--surface') : mk.color, stroke: mk.color, 'stroke-width': 2.5 }, svg);
      el('title', {}, c).textContent = mk.label || '';
      if (mk.label) el('text', { x: X(mk.x) + 9, y: Y(mk.y) - 8, fill: mk.color, 'font-weight': 700 }, svg).textContent = mk.label;
    }
    // hover guide
    const tip = document.createElement('div'); tip.className = 'tip'; tip.hidden = true; container.appendChild(tip);
    const guide = el('line', { y1: m.t, y2: H - m.b, stroke: cssVar('--muted'), 'stroke-dasharray': '2 3', visibility: 'hidden' }, svg);
    const overlay = el('rect', { x: m.l, y: m.t, width: W - m.l - m.r, height: H - m.t - m.b, fill: 'transparent' }, svg);
    const xs = [...new Set(cfg.series.flatMap((s) => s.points.map((p) => p[0])))].sort((a, b) => a - b);
    overlay.addEventListener('mousemove', (ev) => {
      const r = svg.getBoundingClientRect(); const sx = (ev.clientX - r.left) / r.width * W;
      let best = xs[0]; for (const x of xs) if (Math.abs(X(x) - sx) < Math.abs(X(best) - sx)) best = x;
      guide.setAttribute('x1', X(best)); guide.setAttribute('x2', X(best)); guide.setAttribute('visibility', 'visible');
      const lines = cfg.series.map((s) => { const p = s.points.find((q) => q[0] === best); return p && p[1] !== null ? `<span style="color:${s.color}">■</span> ${esc(s.name)}: <b>${fmt(p[1], 5)}</b>` : null; }).filter(Boolean);
      tip.innerHTML = `<div class="muted">${esc(cfg.xLabel || 'x')}: ${cfg.xFmt ? cfg.xFmt(best) : fmt(best, 5)}</div>${lines.join('<br>')}`;
      tip.hidden = false;
      const left = (X(best) / W) * r.width; tip.style.left = Math.min(left + 12, r.width - tip.offsetWidth - 4) + 'px'; tip.style.top = '8px';
    });
    overlay.addEventListener('mouseleave', () => { tip.hidden = true; guide.setAttribute('visibility', 'hidden'); });
    if (cfg.legend !== false) {
      const lg = document.createElement('div'); lg.className = 'legend';
      lg.innerHTML = cfg.series.filter((s) => s.name).map((s) => `<span><i class="${s.dash ? 'dash' : ''}" style="border-color:${s.color}"></i>${esc(s.name)}</span>`).join('')
        + (cfg.markers || []).filter((k) => k.legend).map((k) => `<span><i class="dot" style="background:${k.color}"></i>${esc(k.legend)}</span>`).join('');
      container.appendChild(lg);
    }
    return svg;
  }

  function idiqChart(container, mp) {
    register(container, () => idiqChart(container, mp));
    container.innerHTML = '';
    const [x0, x1] = mp.axes.id_A, [y0, y1] = mp.axes.iq_A;
    const W = widthOf(container, 460); const m = { l: 50, r: 12, t: 12, b: 40 };
    const hMax = window.innerWidth < 700 ? 460 : 560;
    // equal scale on both axes so that the current circle and voltage ellipse keep their true shape
    const sc = Math.min((W - m.l - m.r) / (x1 - x0), (hMax - m.t - m.b) / (y1 - y0));
    m.l += Math.max(0, (W - m.l - m.r - (x1 - x0) * sc) / 2);
    const H = Math.round((y1 - y0) * sc + m.t + m.b);
    const X = (v) => m.l + (v - x0) * sc, Y = (v) => H - m.b - (v - y0) * sc;
    const svg = el('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': 'id-iq constraint map' }); container.appendChild(svg);
    const g = el('g', { class: 'axis' }, svg);
    for (const v of niceTicks(x0, x1, 5)) { el('line', { x1: X(v), x2: X(v), y1: m.t, y2: H - m.b, class: 'gridline' }, g); el('text', { x: X(v), y: H - m.b + 14, 'text-anchor': 'middle' }, g).textContent = fmt(v, 3); }
    for (const v of niceTicks(y0, y1, 8)) { el('line', { x1: m.l, x2: X(x1), y1: Y(v), y2: Y(v), class: 'gridline' }, g); el('text', { x: m.l - 5, y: Y(v) + 4, 'text-anchor': 'end' }, g).textContent = fmt(v, 3); }
    const xr = X(x1);
    el('text', { x: (m.l + xr) / 2, y: H - 6, 'text-anchor': 'middle', class: 'title' }, svg).textContent = 'id [A peak]';
    el('text', { x: m.l - 38, y: (m.t + H - m.b) / 2, 'text-anchor': 'middle', class: 'title', transform: `rotate(-90 ${m.l - 38} ${(m.t + H - m.b) / 2})` }, svg).textContent = 'iq [A peak]';
    const clipId = 'clip' + Math.random().toString(36).slice(2, 8);
    const defs = el('defs', {}, svg); const cp = el('clipPath', { id: clipId }, defs);
    el('rect', { x: m.l, y: m.t, width: X(x1) - m.l, height: H - m.t - m.b }, cp);
    const plot = el('g', { 'clip-path': `url(#${clipId})` }, svg);
    const drawRuns = (runs, color, op) => { let d = ''; for (const [a, b, c, e] of runs) d += `M${X(a).toFixed(1)},${Y(e).toFixed(1)}H${X(b).toFixed(1)}V${Y(c).toFixed(1)}H${X(a).toFixed(1)}Z`; if (d) el('path', { d, fill: color, opacity: op }, plot); };
    if (mp.regions.model_coverage) drawRuns(mp.regions.model_coverage, cssVar('--c5'), 0.06);
    drawRuns(mp.regions.electrical_feasible || [], cssVar('--c1'), 0.14);
    drawRuns(mp.regions.all_limits_feasible || [], cssVar('--c3'), 0.28);
    const db = mp.domain_box; el('rect', { x: X(db.id_A[0]), y: Y(Math.min(db.iq_A[1], y1)), width: X(Math.min(db.id_A[1], x1)) - X(Math.max(db.id_A[0], x0)), height: Y(Math.max(db.iq_A[0], y0)) - Y(Math.min(db.iq_A[1], y1)), fill: 'none', stroke: cssVar('--muted'), 'stroke-dasharray': '4 3' }, plot);
    el('circle', { cx: X(0), cy: Y(0), r: mp.current_limit_A * sc, fill: 'none', stroke: cssVar('--c5'), 'stroke-width': 1.5 }, plot);
    const segs = (list, color, dash, w) => { let d = ''; for (const s of list || []) d += `M${X(s[0]).toFixed(1)},${Y(s[1]).toFixed(1)}L${X(s[2]).toFixed(1)},${Y(s[3]).toFixed(1)}`; if (d) el('path', { d, stroke: color, 'stroke-width': w || 2, fill: 'none', 'stroke-dasharray': dash ? '5 4' : null, 'stroke-linecap': 'round' }, plot); };
    segs(mp.contours.voltage_budget, cssVar('--c1'), false, 2);
    segs(mp.contours.dc_discharge_limit, cssVar('--c2'), true, 1.8);
    segs(mp.contours.dc_charge_limit, cssVar('--c4'), true, 1.8);
    segs(mp.contours.torque_request, cssVar('--text'), false, 2.2);
    if (mp.active_loss_candidate) el('circle', { cx: X(mp.active_loss_candidate.id_A), cy: Y(mp.active_loss_candidate.iq_A), r: 6, fill: cssVar('--surface'), stroke: cssVar('--c6'), 'stroke-width': 2.5 }, plot);
    if (mp.policy_point) el('circle', { cx: X(mp.policy_point.id_A), cy: Y(mp.policy_point.iq_A), r: 6.5, fill: cssVar('--c6'), stroke: cssVar('--surface'), 'stroke-width': 2 }, plot);
    const lg = document.createElement('div'); lg.className = 'legend';
    const L = state.lang === 'ko';
    lg.innerHTML = [
      `<span><i style="border-color:${cssVar('--text')}"></i>${L ? '요구 축토크 곡선' : 'requested shaft-torque curve'}</span>`,
      `<span><i style="border-color:${cssVar('--c1')}"></i>${L ? '전압 예산 경계' : 'voltage budget'}</span>`,
      `<span><i style="border-color:${cssVar('--c5')}"></i>${L ? '전류 한계' : 'current limit'}</span>`,
      `<span><i class="dash" style="border-color:${cssVar('--c2')}"></i>${L ? 'DC 방전 한계' : 'DC discharge limit'}</span>`,
      `<span><i class="dash" style="border-color:${cssVar('--c4')}"></i>${L ? 'DC 충전 한계' : 'DC charge limit'}</span>`,
      `<span><i class="fill" style="background:${cssVar('--c1')};opacity:.3"></i>${L ? '전기적 가능' : 'electrically feasible'}</span>`,
      `<span><i class="fill" style="background:${cssVar('--c3')};opacity:.5"></i>${L ? 'DC 포함 가능' : 'feasible incl. DC'}</span>`,
      `<span><i class="dot" style="background:${cssVar('--c6')}"></i>${L ? '정책 운전점' : 'policy point'}</span>`,
    ].join('');
    container.appendChild(lg);
  }

  function marginBars(container, cons) {
    const rows = cons.filter((c) => c.slack !== null && c.slack !== undefined && c.limit !== null);
    container.innerHTML = '<div class="bars">' + rows.map((c) => {
      const scale = Math.max(Math.abs(c.limit), 1e-9);
      const r = Math.max(-0.5, Math.min(1, c.slack / scale));
      const zero = 33.33; const w = Math.abs(r) / 1.5 * 100; const left = r >= 0 ? zero : zero - w;
      const st = c.state;
      return `<div class="bar-row" title="${esc(c.source)}"><span class="lbl">${esc(c.name)}${st !== 'SATISFIED' ? `<span class="state-tag ${st}">${st}</span>` : ''}</span>
        <div class="bar-track"><div class="zero" style="left:${zero}%"></div><div class="bar-fill ${st}" style="left:${left}%;width:${Math.max(w, 0.8)}%"></div></div>
        <span class="num">${fmt(c.demand, 5)} / ${fmt(c.limit, 5)} · slack <b>${fmt(c.slack, 4)}</b> ${esc(c.unit)}</span></div>`;
    }).join('') + '</div><div class="legend-note muted tiny">' + (state.lang === 'ko' ? '막대 = slack/|limit| (−50%…100% 표시), 0선 왼쪽은 위반' : 'bar = slack/|limit| (shown −50%…100%); left of the zero line is a violation') + '</div>';
  }

  function budgetBar(container, v) {
    const ceil = v.hardware_ceiling_V_peak, bud = v.command_budget_V_peak, dem = v.command_demand_V_peak;
    const max = Math.max(ceil, dem) * 1.02;
    const p = (x) => (x / max * 100).toFixed(2) + '%';
    const col = dem > bud * (1 + 1e-9) ? cssVar('--fail') : (Math.abs(bud - dem) <= bud * 1e-6 ? cssVar('--unknown') : cssVar('--pass'));
    container.innerHTML = `<div class="budget"><div class="seg-a" style="left:0;width:${p(bud)}"></div><div class="seg-r" style="left:${p(bud)};width:calc(${p(ceil)} - ${p(bud)})"></div>
      <div class="dem" style="left:0;width:${p(dem)};background:${col};opacity:.55"></div><span class="lab" style="left:0">${t('budget.demand')} ${fmt(dem, 5)} V</span></div>
      <div class="muted tiny">${t('budget.ceiling')} ${fmt(ceil, 5)} V · ${t('budget.reserve')} ${fmt(v.reserve_V, 4)} V · budget ${fmt(bud, 5)} V · margin <b>${fmt(v.remaining_command_margin_V, 4)} V</b></div>`;
  }

  function powerFlow(container, op) {
    const L = state.lang === 'ko';
    const items = [
      ['P_dc', op.Pdc_W, L ? 'DC 단자 (배터리→인버터 +)' : 'DC terminal (source→inverter +)'],
      ['P_inv', op.Pinv_W, L ? '인버터 손실' : 'inverter loss'],
      ['P_ac', op.Pac_W, L ? '인버터→모터 AC' : 'inverter→motor AC'],
      ['P_cu', op.Pcu_W, L ? '동손' : 'copper loss'],
      ['P_rot', op.Prot_W, L ? '회전 손실' : 'rotational loss'],
      ['P_shaft', op.Pshaft_W, L ? '축 (모터→부하 +)' : 'shaft (motor→load +)'],
    ];
    const max = Math.max(...items.map((i) => Math.abs(i[1] || 0)), 1);
    container.innerHTML = '<div class="bars">' + items.map(([k, v, lab]) => {
      const w = Math.abs(v || 0) / max * 66.6; const pos = (v || 0) >= 0;
      const loss = k === 'P_inv' || k === 'P_cu' || k === 'P_rot';
      const color = loss ? cssVar('--c2') : pos ? cssVar('--c1') : cssVar('--c3');
      return `<div class="bar-row"><span class="lbl">${k} <span class="muted tiny">${esc(lab)}</span></span><div class="bar-track"><div class="zero" style="left:33.33%"></div>
        <div class="bar-fill" style="background:${color};left:${pos ? 33.33 : 33.33 - w}%;width:${Math.max(w, 0.6)}%"></div></div><span class="num"><b>${fmt(v, 6)}</b> W</span></div>`;
    }).join('') + `</div><div class="muted tiny" style="margin-top:6px">${esc(op.energy_mode)} · η = ${fmt(op.efficiency, 5)} — ${esc(op.efficiency_note)}<br>`
      + `${L ? '항등식 잔차' : 'identity residuals'}: ${Object.entries(op.power_identity_residuals_W).filter(([k]) => k.startsWith('P')).map(([k, v]) => `${esc(k)} = ${fmt(v, 2)} W`).join(' · ')}</div>`;
  }

  // ------------------------------------------------------------------ decision page
  function formPayload(form) {
    const f = new FormData(form); const num = (k) => (f.get(k) === '' || f.get(k) === null ? null : Number(f.get(k)));
    const range = f.get('vmode') === 'range';
    const req = { id: f.get('id') || 'REQ-UI', text: f.get('text') || '', torque_Nm: num('torque_Nm'), speed_rpm: num('speed_rpm'),
      Vdc_V: range ? [num('Vdc_V'), num('Vdc_hi')] : num('Vdc_V'), duration_s: num('duration_s'), coolant_temp_C: num('coolant_temp_C') };
    const lim = {}; for (const k of ['discharge_power_max_W', 'charge_power_max_W', 'discharge_current_max_A', 'charge_current_max_A']) if (num(k) !== null) lim[k] = num(k);
    const an = {}; if (f.get('an_dom')) an.dominance = true; if (f.get('an_size')) an.sizing = [{ parameter: 'Vdc_V', range: [400, 800] }];
    if (f.get('an_size') || f.get('an_dom')) an.relaxation = true;
    const body = { requirement: req, analyses: an };
    if (Object.keys(lim).length) body.limits = { discharge_power_max_W: 200000, charge_power_max_W: 100000, discharge_current_max_A: 400, charge_current_max_A: 200, ...lim };
    return body;
  }
  function fillForm(p) {
    const form = $('#req-form'); const r = p.req;
    form.id.value = r.id; form.text.value = r.text; form.torque_Nm.value = r.torque_Nm; form.speed_rpm.value = r.speed_rpm;
    const range = Array.isArray(r.Vdc_V);
    form.querySelector(`input[name=vmode][value=${range ? 'range' : 'point'}]`).checked = true; toggleRange();
    form.Vdc_V.value = range ? r.Vdc_V[0] : r.Vdc_V; if (range) form.Vdc_hi.value = r.Vdc_V[1];
    form.duration_s.value = r.duration_s ?? ''; form.coolant_temp_C.value = r.coolant_temp_C ?? '';
    form.an_dom.checked = !!(p.analyses && p.analyses.dominance); form.an_size.checked = !!(p.analyses && p.analyses.sizing);
  }
  function toggleRange() { const range = $('#req-form').querySelector('input[name=vmode]:checked').value === 'range'; $('.vrange-hi').hidden = !range; }

  async function runEvaluate(body) {
    const out = $('#dec-results'); const btn = $('#req-form .primary');
    btn.disabled = true; out.innerHTML = `<div class="card loading"><div class="muted">${t('loading')}</div></div>`;
    try {
      const rec = await api('evaluate', body);
      state.lastRecord = rec; state.lastPayload = body;
      renderDecision(rec, body);
    } catch (e) {
      out.innerHTML = `<div class="card callout bad"><b>${t('err')}:</b> ${esc(e.message)}${e.data && e.data.status ? ` <span class="pill bad">${esc(e.data.status)}</span>` : ''}</div>`;
      toast(e.message, true);
    } finally { btn.disabled = false; }
  }

  function headline(rec) {
    const v = rec.verdict.status; const c = rec.conditions[0]; const r = rec.requirement; const L = state.lang === 'ko';
    const cond = `${fmt(r.target_Nm)} N·m @ ${fmt(r.conditions.speed_rpm_mechanical, 6)} rpm, Vdc ${Array.isArray(r.conditions.Vdc_V_inverter_dc_terminal) ? r.conditions.Vdc_V_inverter_dc_terminal.join('–') : r.conditions.Vdc_V_inverter_dc_terminal} V`;
    let s = `${cond} — ${t(v === 'FEASIBLE' ? 'hl.pass' : v === 'INFEASIBLE' ? 'hl.fail' : 'hl.unknown')}`;
    if (v === 'FEASIBLE' && c.torque_capability_margin_Nm !== null) s += L ? ` (토크 여유 ${fmt(c.torque_capability_margin_Nm, 4)} N·m)` : ` (torque margin ${fmt(c.torque_capability_margin_Nm, 4)} N·m)`;
    return s;
  }

  function claimCard(c) {
    const name = (CLAIM_NAMES[state.lang] && CLAIM_NAMES[state.lang][c.name]) || c.name;
    const ev = (c.evidence || []).map((e) => `<li><i>${esc(e.kind)}</i>: ${esc(e.summary)}</li>`).join('');
    return `<div class="claim"><div class="name">${esc(name)}</div>${statusChip(c.status)}
      ${c.reasons.length ? `<div class="tiny muted" style="margin-top:4px">${t('reasons')}: ${c.reasons.map(reasonText).map(esc).join(', ')}</div>` : ''}
      ${c.qualifiers.length ? `<div class="qual">${c.qualifiers.map((q) => `<span>${esc(q)}</span>`).join('')}</div>` : ''}
      <div class="detail">${esc(c.detail)}</div>
      ${ev ? `<details><summary>${t('evidence')} (${c.evidence.length})</summary><ul>${ev}</ul></details>` : ''}</div>`;
  }

  function renderDecision(rec, body) {
    const out = $('#dec-results'); const v = rec.verdict; const L = state.lang === 'ko';
    const conds = rec.conditions;
    const firstBad = conds.find((c) => c.requirement_claim_at_this_condition.status !== 'FEASIBLE') || conds[0];
    const c = firstBad; const sol = c.policy_solution; const op = sol.operating_point;
    const traceBad = (k) => ({ cons: op && op.violated_groups.length > 0, conc: v.status !== 'FEASIBLE' })[k] ? 'bad' : '';
    let html = `<div class="card verdict ${v.status}"><div class="big">${t('verdict.' + v.status)}</div><div>
      <div class="headline">${esc(headline(rec))}</div>
      ${v.reasons.length ? `<div class="small">${t('reasons')}: ${v.reasons.map(reasonText).map(esc).join(', ')}</div>` : ''}
      <div class="scope">${esc(v.scope)}</div>
      ${v.qualifiers.length ? `<div class="qual">${v.qualifiers.map((q) => `<span>${esc(q)}</span>`).join('')}</div>` : ''}
      <div class="trace">${['req', 'cond', 'op', 'pow', 'cons', 'conc'].map((k) => `<a href="#sec-${k}" class="${traceBad(k)}">${t('trace.' + k)}</a>`).join('')}</div></div></div>`;
    // requirement + conditions
    const r = rec.requirement;
    html += `<div class="card" id="sec-req"><div class="section-title"><h2>${t('trace.req')} · ${esc(r.req_id)}</h2><span class="anchor">rev ${esc(r.revision)}</span></div>
      <blockquote class="small" style="margin:6px 0;padding:6px 10px;border-left:3px solid var(--border)">${esc(r.original_text)}</blockquote>
      <div class="muted tiny">${esc(r.quantity)} · ${esc(r.port)} · ${L ? '지속시간' : 'duration'}: ${esc(r.duration)}</div></div>`;
    html += `<div class="card" id="sec-cond"><h2>${t('cond.t')}</h2><div class="table-wrap"><table><thead><tr><th>n [rpm]</th><th>Vdc [V]</th><th>${L ? '판정' : 'result'}</th><th>${L ? '토크 여유' : 'torque margin'}</th><th>${L ? '정책 capability' : 'policy capability'}</th></tr></thead><tbody>`
      + conds.map((x) => `<tr><td class="num">${fmt(x.scenario.speed_rpm_mechanical, 6)}</td><td class="num">${fmt(x.scenario.Vdc_V_inverter_dc_terminal, 5)}</td><td>${statusChip(x.requirement_claim_at_this_condition.status)}</td><td class="num">${fmtU(x.torque_capability_margin_Nm, 'N·m', 4)}</td><td class="num">${x.policy_capability ? fmtU(x.policy_capability.achieved_value_Nm, 'N·m', 6) + (x.policy_capability.certified ? ' ✓cert' : ' (sampled)') : '—'}</td></tr>`).join('')
      + `</tbody></table></div>${conds.length > 1 ? `<div class="muted tiny" style="margin-top:6px">${L ? '아래 상세는 첫 번째 미통과 조건 기준입니다.' : 'Details below show the first non-passing condition.'}</div>` : ''}</div>`;
    html += `<div class="card"><h2>${t('sec.claims')}</h2><div class="claims">${sol.claims.map(claimCard).join('')}${c.duration_claim ? claimCard(c.duration_claim) : ''}</div></div>`;
    if (op) {
      const cap = c.policy_capability;
      html += `<div class="card" id="sec-op"><h2>${t('sec.op')}</h2><div class="kv">
        <div><div class="k">${t('k.id')}</div><span class="v">${fmt(op.id_A_peak, 6)}</span><span class="u">A</span></div>
        <div><div class="k">${t('k.iq')}</div><span class="v">${fmt(op.iq_A_peak, 6)}</span><span class="u">A</span></div>
        <div><div class="k">${t('k.ipk')}</div><span class="v">${fmt(op.i_peak_A, 5)}</span><span class="u">A</span></div>
        <div><div class="k">${t('k.irms')}</div><span class="v">${fmt(op.i_phase_rms_A, 5)}</span><span class="u">A</span><div class="s">${esc(op.rms_interpretation)}</div></div>
        <div><div class="k">${t('k.te')}</div><span class="v">${fmt(op.Te_Nm, 6)}</span><span class="u">N·m</span></div>
        <div><div class="k">${t('k.tsh')}</div><span class="v">${fmt(op.Tshaft_Nm, 6)}</span><span class="u">N·m</span></div>
        <div><div class="k">${t('k.cap')}</div><span class="v">${cap ? fmt(cap.achieved_value_Nm, 6) : '—'}</span><span class="u">N·m</span><div class="s">${cap ? (cap.certified ? 'certified' : 'sampled') + ' · ' + cap.active_constraints_at_witness.join(', ') : ''}</div></div>
        <div><div class="k">${t('k.margin')}</div><span class="v">${fmt(c.torque_capability_margin_Nm, 4)}</span><span class="u">N·m</span></div>
        <div><div class="k">${t('k.idc')}</div><span class="v">${fmt(op.Idc_A_average, 5)}</span><span class="u">A</span></div>
        <div><div class="k">f<sub>sw</sub>/f<sub>e</sub></div><span class="v">${fmt(op.pwm_to_electrical_frequency_ratio, 3)}</span><div class="s">${L ? '보고용(임계값 없음)' : 'reported only'}</div></div>
      </div><h3>${L ? '전압 예산 (상 피크)' : 'Voltage budget (phase peak)'}</h3><div id="budget"></div></div>
      <div class="card" id="sec-pow"><h2>${t('sec.power')}</h2><div id="pflow"></div></div>
      <div class="card" id="sec-cons"><h2>${t('sec.cons')}</h2><div id="margins"></div></div>`;
    } else {
      html += `<div class="card" id="sec-op"><h2>${t('sec.op')}</h2><div class="callout bad">${L ? '선택된 운전점 없음: 명시된 운전영역에서 전기적 해가 존재하지 않거나 판정할 수 없습니다.' : 'No operating point: no electrical solution in the declared domain (or undecided).'}</div>
        ${sol.necessary_condition_screens.filter((s) => s.violated).map((s) => `<div class="callout warn"><b>${L ? '필요조건 위반' : 'Necessary condition violated'}:</b> ${esc(s.statement)} <span class="muted tiny">(${esc(s.scope)})</span></div>`).join('')}</div>
        <div class="card" id="sec-pow" hidden></div><div class="card" id="sec-cons" hidden></div>`;
    }
    const wait = L ? 'T–n 곡선 계산 중… (속도 17점 × 양방향, 약 3초)' : 'Computing the T–n envelope… (17 speeds × 2 directions, ~3 s)';
    html += `<div class="card" id="sec-charts"><h2>${t('sec.charts')}</h2><div class="grid-2"><div><div class="chart" id="tn-chart"><div class="loading muted small" style="height:220px;padding:12px">${wait}</div></div></div><div><div class="chart map" id="map-chart"><div class="loading" style="height:220px"></div></div></div></div></div>`;
    html += `<div class="card" id="sec-conc"><h2>${t('sec.conc')}</h2>
      <h3>${t('lim.t')}</h3><ul class="bullets bad">${rec.limiting_factors.map((x) => `<li>${esc(x)}</li>`).join('') || '<li>—</li>'}</ul>
      <h3>${t('next.t')}</h3><ul class="bullets warn">${rec.next_actions.map((x) => `<li>${esc(x)}</li>`).join('') || `<li>${L ? '명시된 범위 안에서 추가 조치 없음 — 범위 밖 항목은 아래 "평가하지 않은 것" 참고' : 'None within the stated scope — see "not evaluated" for what lies outside it'}</li>`}</ul>
      <details><summary class="small">${t('noteval.t')} (${rec.not_evaluated.length})</summary><ul class="bullets">${rec.not_evaluated.map((x) => `<li>${esc(x)}</li>`).join('')}</ul></details></div>`;
    if (rec.analyses && Object.keys(rec.analyses).length) html += `<div class="card" id="sec-an"><h2>${t('sec.analyses')}</h2><div id="an-out"></div></div>`;
    html += `<div class="card" id="sec-record"><h2>${t('sec.record')}</h2><div class="small"><span class="mono">${esc(rec.record_id)}</span></div>
      <div class="muted tiny mono" style="word-break:break-all">sha256 ${esc(rec.input_sha256)}</div>
      <div class="muted tiny">${esc(rec.model.drive_id)} rev ${esc(rec.model.revision)} · ${esc(rec.model.fidelity)} · ${esc(rec.model.provenance.origin)} · ${esc(rec.model.provenance.validation_status)}</div>
      <div class="row-actions"><button class="ghost" id="dl-json">${t('rec.download_json')}</button><button class="ghost" id="dl-md">${t('rec.download_md')}</button><button class="ghost" id="cp-hash">${t('rec.copy')}</button></div></div>`;
    out.innerHTML = html;
    if (op) { budgetBar($('#budget'), op.voltage); powerFlow($('#pflow'), op); marginBars($('#margins'), op.constraints); }
    if (rec.analyses && Object.keys(rec.analyses).length) renderAnalyses($('#an-out'), rec.analyses);
    $('#dl-json').onclick = () => { const o = { ...rec }; delete o.markdown; download(`${rec.record_id}.json`, JSON.stringify(o, null, 2), 'application/json'); };
    $('#dl-md').onclick = () => download(`${rec.record_id}.md`, rec.markdown, 'text/markdown');
    $('#cp-hash').onclick = () => { navigator.clipboard && navigator.clipboard.writeText(rec.input_sha256); toast('sha256 copied'); };
    const sc = c.scenario; const T = rec.requirement.target_Nm;
    drawTnForDecision(sc.Vdc_V_inverter_dc_terminal, sc.speed_rpm_mechanical, T, c.requirement_claim_at_this_condition.status, c.policy_capability, body && body.preset);
    api('map', { speed_rpm: sc.speed_rpm_mechanical, Vdc_V: sc.Vdc_V_inverter_dc_terminal, torque_Nm: T, preset: body && body.preset })
      .then((mp) => idiqChart($('#map-chart'), mp)).catch((e) => { $('#map-chart').innerHTML = `<div class="muted small">${esc(e.message)}</div>`; });
    if (out.getBoundingClientRect().top > window.innerHeight * 0.6) out.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  async function getCurve(vdc) {
    const k = String(vdc);
    if (!state.curveCache[k]) state.curveCache[k] = api('curve', { Vdc_V: vdc });
    return state.curveCache[k];
  }
  async function drawTnForDecision(vdc, n, T, status, cap, preset) {
    const box = $('#tn-chart');
    try {
      const cv = await getCurve(vdc); const L = state.lang === 'ko';
      const col = status === 'FEASIBLE' ? cssVar('--pass') : status === 'INFEASIBLE' ? cssVar('--fail') : cssVar('--unknown');
      const markers = [{ x: n, y: T, color: col, label: L ? '요구' : 'request', legend: L ? `요구점 (${t('st.' + status)})` : `request (${status})` }];
      if (cap && cap.achieved_value_Nm !== null) markers.push({ x: n, y: cap.achieved_value_Nm, color: cssVar('--c1'), hollow: true, r: 5, legend: L ? '해당 속도 capability' : 'capability at this speed' });
      lineChart(box, {
        height: 330, xLabel: 'n [rpm]', yLabel: 'T_shaft [N·m]', aria: 'torque speed capability',
        series: [
          { name: `${L ? '정책(DC 포함)' : 'policy incl. DC'} max @ ${vdc} V`, color: cssVar('--c1'), points: cv.rows.map((r) => [r.speed_rpm, r.policy_max_Nm]) },
          { name: `${L ? '전기적 한계' : 'electrical'} max`, color: cssVar('--c1'), dash: true, width: 1.5, points: cv.rows.map((r) => [r.speed_rpm, r.electrical_max_Nm]) },
          { name: `${L ? '정책(DC 포함)' : 'policy incl. DC'} min`, color: cssVar('--c3'), points: cv.rows.map((r) => [r.speed_rpm, r.policy_min_Nm]) },
          { name: `${L ? '전기적 한계' : 'electrical'} min`, color: cssVar('--c3'), dash: true, width: 1.5, points: cv.rows.map((r) => [r.speed_rpm, r.electrical_min_Nm]) },
        ], markers,
      });
    } catch (e) { box.innerHTML = `<div class="muted small">${esc(e.message)}</div>`; }
  }

  function renderAnalyses(box, an) {
    const L = state.lang === 'ko'; let h = '';
    if (an.comparison) {
      h += `<h3>${L ? '조건 비교' : 'Scenario comparison'}</h3><ul class="bullets">${an.comparison.changes_vs_baseline.map((x) => `<li>${esc(x)}</li>`).join('')}</ul>`;
    }
    if (an.sizing) for (const s of an.sizing) h += sizingHtml(s);
    if (an.relaxation) {
      const rl = an.relaxation;
      h += `<h3>${L ? '요구 충족에 필요한 최소 완화 (진단)' : 'Smallest relaxation that meets the request (diagnostic)'}</h3><div class="table-wrap"><table><thead><tr><th>constraint</th><th>${L ? '단독으로 충분' : 'sufficient alone'}</th><th class="num">${L ? '최소 완화' : 'min. relaxation'}</th></tr></thead><tbody>`
        + rl.single.map((r) => `<tr><td>${esc(r.constraint)}</td><td>${r.sufficient_alone ? '<span class="pill ok">yes</span>' : '<span class="pill bad">no</span>'}</td><td class="num">${r.minimal_relative_relaxation === null ? '—' : '+' + fmt(r.minimal_relative_relaxation * 100, 3) + '%'}</td></tr>`).join('')
        + `</tbody></table></div>` + (rl.joint.length ? `<div class="callout warn"><b>${t('dom.joint')}:</b> ${rl.joint.map((j) => `${j.constraints.join(' + ')} (+${fmt(j.minimal_relative_relaxation_each * 100, 3)}% ${L ? '각각' : 'each'})`).join('; ')}</div>` : '');
    }
    if (an.dominance) h += dominanceHtml(an.dominance);
    box.innerHTML = h;
  }

  function sizingHtml(s) {
    const L = state.lang === 'ko'; const p = s.parameter;
    const lo = s.search_range[0], hi = s.search_range[1];
    const cells = s.status_samples.map(([x, st]) => `<div title="${fmt(x, 5)}: ${st}" style="flex:1;height:14px;background:${st === 'FEASIBLE' ? cssVar('--pass') : st === 'INFEASIBLE' ? cssVar('--fail') : cssVar('--unknown')};opacity:.75"></div>`).join('');
    return `<h3>${L ? '역설계' : 'Sizing'}: ${esc(p.parameter)} <span class="pill unk">${esc(p.change_kind)}</span></h3>
      <div style="display:flex;gap:1px;border-radius:6px;overflow:hidden">${cells}</div><div class="muted tiny" style="display:flex;justify-content:space-between"><span>${fmt(lo, 5)} ${esc(p.unit)}</span><span>${fmt(hi, 5)} ${esc(p.unit)}</span></div>
      ${s.minimal_feasible_value !== null ? `<div class="callout good">${t('size.min')}: <b>${fmt(s.minimal_feasible_value, 6)} ${esc(p.unit)}</b>${s.solution_at_minimal_value ? ` — active: ${s.solution_at_minimal_value.active_constraints.join(', ')}` : ''}</div>` : `<div class="callout bad">${t('size.none')}</div>`}
      <ul class="bullets tiny">${s.notes.map((n) => `<li>${esc(n)}</li>`).join('')}</ul>`;
  }
  function dominanceHtml(d) {
    const L = state.lang === 'ko'; const max = Math.max(...d.single.map((r) => Math.abs(r.gain_Nm || 0)), 1e-9);
    return `<h3>${L ? '병목 분석' : 'Dominance'} — ${L ? '기준 capability' : 'base capability'} ${fmt(d.base_policy_capability_Nm, 6)} N·m</h3>
      <div class="bars">${d.single.map((r) => `<div class="bar-row"><span class="lbl">${esc(r.constraint)}${r.active_at_base ? `<span class="state-tag ACTIVE">${t('dom.active')}</span>` : ''}</span>
        <div class="bar-track"><div class="bar-fill ${r.classification === 'limiting' ? 'SATISFIED' : ''}" style="left:0;width:${Math.abs(r.gain_Nm || 0) / max * 100}%;background:${r.classification === 'limiting' ? cssVar('--c1') : cssVar('--c5')}"></div></div>
        <span class="num">+${fmt(r.gain_Nm, 4)} N·m · ${esc(r.classification)}</span></div>`).join('')}</div>
      ${d.joint.length ? `<div class="callout warn"><b>${t('dom.joint')}:</b> ${d.joint.map((j) => `${j.constraints.join(' + ')} → +${fmt(j.gain_Nm, 4)} N·m`).join('; ')}</div>` : ''}
      <div class="muted tiny">${esc(d.relaxation)} · ${esc(d.recomputation)}</div>`;
  }

  function initDecision() {
    const box = $('#presets');
    box.innerHTML = state.info.presets.map((p) => `<button type="button" class="preset" data-key="${p.key}" title="${esc(p.hint[state.lang] || p.hint.en)}">${esc(p.title[state.lang] || p.title.en)}</button>`).join('');
    $$('.preset', box).forEach((b) => b.addEventListener('click', () => {
      const p = state.info.presets.find((x) => x.key === b.dataset.key);
      $$('.preset', box).forEach((x) => x.classList.toggle('on', x === b));
      fillForm(p); const body = formPayload($('#req-form')); body.preset = p.key; if (p.analyses) body.analyses = p.analyses;
      runEvaluate(body);
    }));
    $$('#req-form input[name=vmode]').forEach((r) => r.addEventListener('change', toggleRange));
    $('#req-form').addEventListener('submit', (e) => { e.preventDefault(); $$('.preset', box).forEach((x) => x.classList.remove('on')); runEvaluate(formPayload(e.target)); });
    fillForm(state.info.presets[0]);
  }

  // ------------------------------------------------------------------ capability page
  function renderCapChips() {
    $('#cap-vdc').innerHTML = state.capVdc.map((v) => `<span class="chip-btn">${v} V<button class="x ghost small" data-v="${v}" aria-label="remove">×</button></span>`).join('');
    $$('#cap-vdc [data-v]').forEach((b) => b.onclick = () => { state.capVdc = state.capVdc.filter((x) => x !== Number(b.dataset.v)); renderCapChips(); });
  }
  async function runCapability() {
    const box = $('#cap-chart'); busy(box, true); const L = state.lang === 'ko';
    try {
      const curves = await Promise.all(state.capVdc.map((v) => getCurve(v)));
      const palette = ['--c1', '--c2', '--c3', '--c4', '--c6'].map(cssVar);
      const series = [];
      curves.forEach((cv, i) => {
        const col = palette[i % palette.length];
        series.push({ name: `${cv.Vdc_V} V ${L ? '정책' : 'policy'}`, color: col, points: cv.rows.map((r) => [r.speed_rpm, r.policy_max_Nm]) });
        series.push({ name: `${cv.Vdc_V} V ${L ? '전기적' : 'electrical'}`, color: col, dash: true, width: 1.4, points: cv.rows.map((r) => [r.speed_rpm, r.electrical_max_Nm]) });
        series.push({ name: '', color: col, points: cv.rows.map((r) => [r.speed_rpm, r.policy_min_Nm]) });
        series.push({ name: '', color: col, dash: true, width: 1.4, points: cv.rows.map((r) => [r.speed_rpm, r.electrical_min_Nm]) });
      });
      busy(box, false);
      lineChart(box, { height: 420, xLabel: 'n [rpm]', yLabel: 'T_shaft [N·m]', series });
      const speeds = curves[0].rows.map((r) => r.speed_rpm);
      $('#cap-table').innerHTML = `<table><thead><tr><th class="num">n [rpm]</th>${curves.map((cv) => `<th class="num">${cv.Vdc_V} V max</th><th>${L ? '제한' : 'limited by'}</th><th class="num">min</th>`).join('')}</tr></thead><tbody>`
        + speeds.map((n, i) => `<tr><td class="num">${fmt(n, 6)}</td>${curves.map((cv) => { const r = cv.rows[i]; return `<td class="num">${fmt(r.policy_max_Nm, 5)}</td><td class="tiny">${(r.policy_max_limited_by || []).join(', ')}</td><td class="num">${fmt(r.policy_min_Nm, 5)}</td>`; }).join('')}</tr>`).join('') + '</tbody></table>';
    } catch (e) { busy(box, false); box.innerHTML = `<div class="callout bad">${esc(e.message)}</div>`; }
  }
  function initCapability() {
    renderCapChips();
    $('#cap-add').onclick = () => { const v = Number($('#cap-vdc-add').value); if (v > 0 && !state.capVdc.includes(v)) { state.capVdc.push(v); state.capVdc.sort((a, b) => a - b); renderCapChips(); } $('#cap-vdc-add').value = ''; };
    $('#cap-run').onclick = runCapability;
  }

  // ------------------------------------------------------------------ design page
  const PARAMS = [
    ['Vdc_V', 'boundary', 'V'], ['I_peak_max_A', 'hardware', 'A'], ['voltage_reserve_fraction', 'design', '-'],
    ['discharge_power_max_W', 'boundary', 'W'], ['discharge_current_max_A', 'boundary', 'A'], ['charge_power_max_W', 'boundary', 'W'],
    ['charge_current_max_A', 'boundary', 'A'], ['id_min_A', 'design', 'A'], ['voltage_budget_scale', 'diagnostic', '-'],
    ['psi_pm_Wb', 'diagnostic', 'Wb'], ['Rs_ohm', 'data', 'Ω'],
  ];
  function initDesign() {
    const sel = $('#size-form select[name=parameter]');
    sel.innerHTML = PARAMS.map(([p, k, u]) => `<option value="${p}">${p} [${u}] · ${k}</option>`).join('');
    $('#size-form').addEventListener('submit', async (e) => {
      e.preventDefault(); const f = new FormData(e.target); const out = $('#size-out'); busy(out, true);
      try {
        const r = await api('sizing', { torque_Nm: +f.get('torque_Nm'), speed_rpm: +f.get('speed_rpm'), Vdc_V: +f.get('Vdc_V'), parameter: f.get('parameter'), range: [+f.get('lo'), +f.get('hi')], samples: 41 });
        out.innerHTML = sizingHtml(r);
      } catch (err) { out.innerHTML = `<div class="callout bad">${esc(err.message)}</div>`; } finally { busy(out, false); }
    });
    $('#dom-form').addEventListener('submit', async (e) => {
      e.preventDefault(); const f = new FormData(e.target); const out = $('#dom-out'); busy(out, true);
      try { out.innerHTML = dominanceHtml(await api('dominance', { speed_rpm: +f.get('speed_rpm'), Vdc_V: +f.get('Vdc_V'), direction: +f.get('direction') })); }
      catch (err) { out.innerHTML = `<div class="callout bad">${esc(err.message)}</div>`; } finally { busy(out, false); }
    });
  }

  // ------------------------------------------------------------------ safety page
  let ftti = null;
  function renderFttiTable() {
    const evs = ftti.events;
    const opt = (sel) => evs.map((e) => `<option ${e === sel ? 'selected' : ''}>${esc(e)}</option>`).join('');
    $('#ftti-table').innerHTML = `<thead><tr><th>ID</th><th>from</th><th>to</th><th>owner</th><th class="num">min</th><th class="num">nom</th><th class="num">max [ms]</th><th class="num">period</th><th></th></tr></thead><tbody>`
      + ftti.items.map((it, i) => `<tr data-i="${i}"><td><input data-k="id" value="${esc(it.id)}"></td><td><select data-k="from">${opt(it.from)}</select></td><td><select data-k="to">${opt(it.to)}</select></td>
        <td><input data-k="owner" value="${esc(it.owner || '')}"></td><td><input data-k="min_ms" type="number" step="any" value="${it.min_ms ?? ''}"></td><td><input data-k="nom_ms" type="number" step="any" value="${it.nom_ms ?? ''}"></td>
        <td><input data-k="max_ms" type="number" step="any" value="${it.max_ms ?? ''}"></td><td><input data-k="period_ms" type="number" step="any" value="${it.period_ms ?? ''}"></td><td><button class="ghost small" data-del="${i}" aria-label="delete">×</button></td></tr>`).join('')
      + `</tbody><tfoot><tr><td colspan="9" class="muted tiny">events: <input id="ftti-events" value="${esc(evs.join(', '))}" style="width:100%"></td></tr></tfoot>`;
    $$('#ftti-table [data-k]').forEach((inp) => inp.addEventListener('change', () => {
      const i = +inp.closest('tr').dataset.i; const k = inp.dataset.k; const v = inp.value;
      ftti.items[i][k] = ['min_ms', 'nom_ms', 'max_ms', 'period_ms'].includes(k) ? (v === '' ? null : Number(v)) : v;
    }));
    $$('#ftti-table [data-del]').forEach((b) => b.onclick = () => { ftti.items.splice(+b.dataset.del, 1); renderFttiTable(); });
    $('#ftti-events').addEventListener('change', (e) => { ftti.events = e.target.value.split(',').map((s) => s.trim()).filter(Boolean); renderFttiTable(); });
  }
  function gantt(box, r) {
    register(box, () => gantt(box, r));
    const L = state.lang === 'ko';
    const counted = r.items.filter((i) => i.counted);
    const tAt = {}; let acc = 0; tAt[r.events[0]] = 0;
    for (const e of r.events.slice(1)) { const it = counted.find((i) => i.to === e); if (it) { acc = (tAt[it.from] ?? acc) + (it.worst_s || 0); } tAt[e] = acc; }
    const total = Math.max(r.ftti_s, r.worst_s || 0) * 1.08; const W = widthOf(box, 720), rowH = 24, m = { l: 110, r: 16, t: 10, b: 34 };
    const H = m.t + m.b + rowH * r.items.length;
    const X = (s) => m.l + s / total * (W - m.l - m.r);
    const svg = el('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': 'timing chain' });
    const dup = new Set(r.duplicate_budgets.flatMap((d) => d.items));
    r.items.forEach((it, i) => {
      const y = m.t + i * rowH; const x0 = X(tAt[it.from] ?? 0); const x1 = it.worst_s !== null ? X((tAt[it.from] ?? 0) + it.worst_s) : x0 + 2;
      el('text', { x: m.l - 6, y: y + 15, 'text-anchor': 'end' }, svg).textContent = it.id;
      const col = dup.has(it.id) && !it.counted ? cssVar('--unknown') : it.owner === 'SW' ? cssVar('--c1') : it.owner === 'HW' ? cssVar('--c3') : cssVar('--c4');
      el('rect', { x: x0, y: y + 3, width: Math.max(2, x1 - x0), height: rowH - 7, rx: 4, fill: col, opacity: it.counted ? 0.85 : 0.35, stroke: dup.has(it.id) ? cssVar('--unknown') : 'none', 'stroke-width': 2 }, svg);
    });
    for (const v of niceTicks(0, total, 8)) { el('line', { x1: X(v), x2: X(v), y1: m.t, y2: H - m.b, class: 'gridline' }, svg); el('text', { x: X(v), y: H - m.b + 14, 'text-anchor': 'middle' }, svg).textContent = fmt(v * 1e3, 3); }
    el('line', { x1: X(r.ftti_s), x2: X(r.ftti_s), y1: m.t, y2: H - m.b, stroke: cssVar('--fail'), 'stroke-width': 2, 'stroke-dasharray': '6 4' }, svg);
    el('text', { x: X(r.ftti_s) - 4, y: H - 4, 'text-anchor': 'end', fill: cssVar('--fail') }, svg).textContent = `FTTI ${fmt(r.ftti_s * 1e3, 4)} ms`;
    if (r.worst_s) el('line', { x1: X(r.worst_s), x2: X(r.worst_s), y1: m.t, y2: H - m.b, stroke: cssVar('--text'), 'stroke-width': 1.5 }, svg);
    box.innerHTML = ''; const c = document.createElement('div'); c.className = 'chart'; c.appendChild(svg); box.appendChild(c);
    const lg = document.createElement('div'); lg.className = 'legend';
    lg.innerHTML = `<span><i class="fill" style="background:${cssVar('--c3')}"></i>HW</span><span><i class="fill" style="background:${cssVar('--c1')}"></i>SW</span><span><i class="fill" style="background:${cssVar('--c4')}"></i>System</span><span><i class="fill" style="background:${cssVar('--unknown')};opacity:.4"></i>${L ? '중복 예산(합산 제외)' : 'duplicate budget (not summed)'}</span><span><i style="border-color:${cssVar('--text')}"></i>${L ? '최악 반응시간' : 'worst case'}</span>`;
    box.appendChild(lg);
  }
  async function runFtti() {
    const out = $('#ftti-out'); busy(out, true); const L = state.lang === 'ko';
    const body = { ...ftti, ftti_ms: +$('#ftti-ms').value, fdti_budget_ms: $('#fdti-ms').value === '' ? null : +$('#fdti-ms').value, frti_budget_ms: $('#frti-ms').value === '' ? null : +$('#frti-ms').value };
    try {
      const r = await api('timing', body);
      out.innerHTML = `<div style="margin:12px 0">${statusChip(r.claim.status)} <b>${esc(r.claim.detail)}</b></div>
        <div class="kv"><div><div class="k">best</div><span class="v">${fmt(r.best_s * 1e3, 4)}</span><span class="u">ms</span></div><div><div class="k">nominal</div><span class="v">${r.nominal_s === null ? '—' : fmt(r.nominal_s * 1e3, 4)}</span><span class="u">ms</span></div>
        <div><div class="k">worst</div><span class="v">${r.worst_s === null ? '—' : fmt(r.worst_s * 1e3, 4)}</span><span class="u">ms</span></div><div><div class="k">margin</div><span class="v">${r.margin_s === null ? '—' : fmt(r.margin_s * 1e3, 4)}</span><span class="u">ms</span></div>
        <div><div class="k">FDTI worst</div><span class="v">${r.fdti_worst_s === null ? '—' : fmt(r.fdti_worst_s * 1e3, 4)}</span><span class="u">ms</span></div><div><div class="k">FRTI worst</div><span class="v">${r.frti_worst_s === null ? '—' : fmt(r.frti_worst_s * 1e3, 4)}</span><span class="u">ms</span></div></div>
        ${r.duplicate_budgets.length ? `<div class="callout warn"><b>${L ? '중복 예산 검출' : 'Duplicate budgets detected'} (${r.duplicate_budgets.length})</b><ul class="bullets">${r.duplicate_budgets.map((d) => `<li>${esc(d.message)} <span class="muted">[${d.owners.join(' / ')}]</span></li>`).join('')}</ul></div>` : ''}
        ${r.gaps.length ? `<div class="callout bad">${L ? '예산 없는 구간' : 'Unbudgeted intervals'}: ${r.gaps.map(esc).join(', ')}</div>` : ''}
        ${r.budget_checks.length ? `<div class="small">${r.budget_checks.map((b) => `${esc(b.budget)} ${pill(b.ok)}`).join(' · ')}</div>` : ''}
        <div id="gantt" style="margin-top:10px"></div><ul class="bullets tiny">${r.notes.map((n) => `<li>${esc(n)}</li>`).join('')}</ul>`;
      gantt($('#gantt'), r);
    } catch (e) { out.innerHTML = `<div class="callout bad">${esc(e.message)}</div>`; } finally { busy(out, false); }
  }
  function formNums(form) { const o = {}; new FormData(form).forEach((v, k) => { o[k] = v === '' ? null : (isNaN(Number(v)) ? v : Number(v)); }); return o; }
  function initSafety() {
    $$('.subtabs button').forEach((b) => b.addEventListener('click', () => {
      $$('.subtabs button').forEach((x) => x.setAttribute('aria-selected', String(x === b)));
      $$('.sub').forEach((s) => { s.hidden = s.dataset.subpage !== b.dataset.sub; });
    }));
    ftti = JSON.parse(JSON.stringify(state.info.example_timing));
    $('#ftti-ms').value = ftti.ftti_ms; renderFttiTable();
    $('#ftti-add').onclick = () => { ftti.items.push({ id: 'NEW', from: ftti.events[0], to: ftti.events[1], owner: 'SW', max_ms: 1 }); renderFttiTable(); };
    $('#ftti-run').onclick = runFtti;
    $('#dis-form').addEventListener('submit', async (e) => {
      e.preventDefault(); const out = $('#dis-out'); busy(out, true); const b = formNums(e.target); const L = state.lang === 'ko';
      try {
        const r = await api('discharge', b);
        out.innerHTML = `<div style="margin:12px 0">${statusChip(r.claim.status)} ${esc(r.claim.detail)}</div><div class="kv">
          <div><div class="k">R max</div><span class="v">${fmt(r.R_max_ohm, 5)}</span><span class="u">Ω</span></div><div><div class="k">I₀</div><span class="v">${fmt(r.I0_A, 4)}</span><span class="u">A</span></div>
          <div><div class="k">P₀</div><span class="v">${fmt(r.P0_W, 4)}</span><span class="u">W</span></div><div><div class="k">E_R</div><span class="v">${fmt(r.E_R_J, 4)}</span><span class="u">J</span></div>
          <div><div class="k">t_reach</div><span class="v">${fmt(r.t_reach_s, 4)}</span><span class="u">s</span></div><div><div class="k">E_C</div><span class="v">${fmt(r.stored_energy_J, 4)}</span><span class="u">J</span></div>
          ${r.back_emf_ll_peak_V !== undefined ? `<div><div class="k">${L ? '역기전력 LL 피크' : 'back-EMF LL peak'}</div><span class="v">${fmt(r.back_emf_ll_peak_V, 4)}</span><span class="u">V</span></div>` : ''}</div><div class="chart" id="dis-chart"></div>`;
        const tau = r.tau_s, pts = []; for (let i = 0; i <= 60; i++) { const tt = i / 60 * Math.max(r.t_target_s, r.t_reach_s) * 1.2; pts.push([tt, r.V0_V * Math.exp(-tt / tau)]); }
        lineChart($('#dis-chart'), { height: 240, xLabel: 't [s]', yLabel: 'V_dc [V]', series: [{ name: 'V(t)', color: cssVar('--c1'), points: pts }], hlines: [{ y: r.Vf_V, color: cssVar('--c2'), label: 'V_f' }], vlines: [{ x: r.t_target_s, color: cssVar('--fail'), label: 't_target' }] });
      } catch (err) { out.innerHTML = `<div class="callout bad">${esc(err.message)}</div>`; } finally { busy(out, false); }
    });
    $('#ov-form').addEventListener('submit', async (e) => {
      e.preventDefault(); const out = $('#ov-out'); busy(out, true); const b = formNums(e.target); const L = state.lang === 'ko';
      try {
        const r = await api('overvoltage', b);
        out.innerHTML = `<div style="margin:12px 0">${statusChip(r.claim.status)} ${esc(r.claim.detail)}</div><div class="kv">
          <div><div class="k">${L ? '유입 회생 전력' : 'regen power in'}</div><span class="v">${fmt(r.P_in_W, 5)}</span><span class="u">W</span></div>
          <div><div class="k">${L ? '한계까지 시간' : 'time to limit'}</div><span class="v">${fmt(r.time_to_limit_constant_power_s * 1e3, 4)}</span><span class="u">ms</span></div>
          <div><div class="k">${L ? '허용 반응시간' : 'max reaction'}</div><span class="v">${fmt(r.max_reaction_time_s * 1e3, 4)}</span><span class="u">ms</span></div>
          <div><div class="k">V peak</div><span class="v">${fmt(r.V_peak_V, 5)}</span><span class="u">V</span></div><div><div class="k">dV/dt₀</div><span class="v">${fmt(r.dVdt_initial_V_per_s / 1000, 4)}</span><span class="u">V/ms</span></div></div>
          ${r.notes.map((n) => `<div class="callout warn">${esc(n)}</div>`).join('')}<div class="chart" id="ov-chart"></div>`;
        const pts = []; const tEnd = Math.max(r.max_reaction_time_s, r.reaction_time_s || 0) * 1.4;
        for (let i = 0; i <= 60; i++) { const tt = i / 60 * tEnd; pts.push([tt * 1e3, Math.sqrt(r.V1_V ** 2 + 2 * r.P_in_W * tt / r.C_F)]); }
        lineChart($('#ov-chart'), { height: 240, xLabel: 't [ms]', yLabel: 'V_dc [V]', series: [{ name: L ? '일정 전력 유입 시' : 'constant power in', color: cssVar('--c2'), points: pts }], hlines: [{ y: r.V_limit_V, color: cssVar('--fail'), label: 'V_lim' }], vlines: r.reaction_time_s ? [{ x: r.reaction_time_s * 1e3, color: cssVar('--text'), label: L ? '반응' : 'reaction' }] : [] });
      } catch (err) { out.innerHTML = `<div class="callout bad">${esc(err.message)}</div>`; } finally { busy(out, false); }
    });
    $('#ss-form').addEventListener('submit', async (e) => {
      e.preventDefault(); const out = $('#ss-out'); busy(out, true); const b = formNums(e.target); const L = state.lang === 'ko';
      b.rules = [{ rule_id: 'PRJ-LV-FW', when: { Vdc_below_V: b.rule_v }, require: 'FREEWHEEL', basis: L ? '프로젝트/고객 규칙 (물리 아님)' : 'project/customer rule (not physics)' }];
      try {
        const r = await api('safe_state', b);
        const risk = (s) => /HIGH|UNCONTROLLED|above|exceeds/i.test(s) ? 'risk-high' : /UNKNOWN/.test(s) ? 'risk-mid' : '';
        out.innerHTML = `<div style="margin:12px 0">${statusChip(r.claim.status)} ${esc(r.claim.detail)}</div><div class="compare">${r.candidates.map((c) => `<div class="col"><h3 style="margin-top:0">${esc(c.candidate)}</h3><dl>${Object.entries(c).filter(([k]) => k !== 'candidate').map(([k, v]) => `<dt>${esc(k.replaceAll('_', ' '))}</dt><dd class="${typeof v === 'string' ? risk(v) : ''}">${typeof v === 'number' ? fmt(v, 5) : esc(v)}</dd>`).join('')}</dl></div>`).join('')}</div>
          <h3>${L ? '프로젝트 규칙 (물리와 분리)' : 'Project rules (separate from physics)'}</h3><ul class="bullets">${r.project_rules.map((x) => `<li>${esc(x.rule_id)}: ${x.applies ? `<b>${L ? '적용' : 'applies'}</b> → require ${esc(x.require)}` : (L ? '미적용' : 'not applicable')} <span class="muted">(${esc(x.basis)})</span></li>`).join('')}</ul>
          <ul class="bullets tiny">${r.notes.map((n) => `<li>${esc(n)}</li>`).join('')}</ul>`;
      } catch (err) { out.innerHTML = `<div class="callout bad">${esc(err.message)}</div>`; } finally { busy(out, false); }
    });
  }

  // ------------------------------------------------------------------ thermal page
  function initThermal() {
    const ta = $('#th-form textarea[name=model]'); ta.value = JSON.stringify(state.info.example_thermal, null, 2);
    $('#th-form').addEventListener('submit', async (e) => {
      e.preventDefault(); const out = $('#th-out'); busy(out, true); const L = state.lang === 'ko';
      const b = formNums(e.target);
      try { b.model = JSON.parse(ta.value); } catch (err) { toast('thermal model JSON: ' + err.message, true); busy(out, false); return; }
      try {
        const r = await api('thermal', b); const av = r.availability; const q = r.request;
        const rows = av.rows.filter((x) => x.torque_Nm !== null);
        const finite = rows.filter((x) => x.duration_s !== 'Infinity' && Number.isFinite(x.duration_s));
        const cont = rows.find((x) => x.duration_s === 'Infinity' || !Number.isFinite(x.duration_s));
        let story = '';
        if (q && q.time_to_first_limit_s !== undefined && cont) {
          const tl = q.time_to_first_limit_s; const inf = tl === 'Infinity' || !Number.isFinite(tl);
          story = L
            ? `냉각수 ${fmt(b.coolant_temp_C, 4)} °C에서 ${fmt(b.torque_Nm, 4)} N·m는 ${inf ? '열 한계에 도달하지 않음' : `약 <b>${fmt(tl, 3)} s</b> 유지 가능`}, 이후 열적 가용 토크는 <b>${fmt(cont.torque_Nm, 4)} N·m</b>(연속)로 감소 — ${av.validated ? '검증된 열모델' : '미검증 열모델 기반 추정치'}`
            : `At ${fmt(b.coolant_temp_C, 4)} °C coolant, ${fmt(b.torque_Nm, 4)} N·m ${inf ? 'never reaches a thermal limit' : `is sustainable for about <b>${fmt(tl, 3)} s</b>`}; thereafter the thermal capability falls to <b>${fmt(cont.torque_Nm, 4)} N·m</b> (continuous) — ${av.validated ? 'validated thermal model' : 'estimate from an unvalidated thermal model'}`;
        }
        out.innerHTML = `${story ? `<div class="callout ${av.validated ? 'good' : 'warn'}" style="font-size:14px">${story}</div>` : ''}${q ? `<div style="margin:12px 0">${statusChip(q.claim.status)} <b>${esc(q.claim.quantity)}</b> — ${esc(q.claim.detail)} ${q.claim.qualifiers.map((x) => `<span class="pill unk">${esc(x)}</span>`).join(' ')}</div>` : ''}
          <div class="muted small">${esc(av.status_note)}</div><div class="chart" id="th-chart"></div>
          <div class="table-wrap"><table><thead><tr><th class="num">${L ? '지속시간' : 'duration'} [s]</th><th class="num">${L ? '가용 토크' : 'available torque'} [N·m]</th><th>${L ? '제한' : 'limited by'}</th></tr></thead><tbody>${av.rows.map((x) => `<tr><td class="num">${fmt(x.duration_s, 4)}</td><td class="num">${fmt(x.torque_Nm, 5)}</td><td>${esc(x.limited_by)}</td></tr>`).join('')}</tbody></table></div>
          ${q && q.nodes ? `<h3>${L ? '요청점 노드 온도' : 'Node temperatures at the request'}</h3><div class="table-wrap"><table><thead><tr><th>node</th><th class="num">P [W]</th><th class="num">T(t) [°C]</th><th class="num">limit</th><th class="num">${L ? '한계 도달' : 'time to limit'} [s]</th></tr></thead><tbody>${q.nodes.map((n) => `<tr><td>${esc(n.node)}</td><td class="num">${fmt(n.power_W, 4)}</td><td class="num">${fmt(n.temperature_at_duration_C, 4)}</td><td class="num">${fmt(n.limit_C, 4)}</td><td class="num">${fmt(n.time_to_limit_s, 4)}</td></tr>`).join('')}</tbody></table></div>` : ''}
          <ul class="bullets tiny">${av.assumptions.map((x) => `<li>${esc(x)}</li>`).join('')}</ul>`;
        const pts = finite.map((x) => [x.duration_s, x.torque_Nm]);
        const hl = [{ y: av.static_capability_Nm, color: cssVar('--c5'), label: L ? '정적 capability' : 'static capability' }];
        if (cont) hl.push({ y: cont.torque_Nm, color: cssVar('--c3'), label: L ? '연속(추정)' : 'continuous (est.)' });
        const mk = q ? [{ x: b.duration_s, y: b.torque_Nm, color: q.claim.status === 'FEASIBLE' ? cssVar('--pass') : q.claim.status === 'INFEASIBLE' ? cssVar('--fail') : cssVar('--unknown'), label: L ? '요청' : 'request' }] : [];
        lineChart($('#th-chart'), { height: 300, xLog: true, xTicks: [1, 3, 10, 30, 100, 300, 1000].filter((v) => v <= Math.max(...pts.map((p) => p[0])) * 1.01), xFmt: (v) => `${v} s`, xLabel: L ? '지속시간 (로그)' : 'duration (log)', yLabel: 'T_shaft [N·m]', series: [{ name: L ? '가용 토크' : 'available torque', color: cssVar('--c2'), points: pts, dots: true }], hlines: hl, markers: mk });
      } catch (err) { out.innerHTML = `<div class="callout bad">${esc(err.message)}</div>`; } finally { busy(out, false); }
    });
  }

  // ------------------------------------------------------------------ V&V page
  const LIMITS = {
    ko: ['정상상태 기본파 모델만: PWM 리플·순간 피크·SOA·과도 응답은 평가하지 않음', '합성 fixture로 검증: 하드웨어·공급사 데이터·외부 시뮬레이터 검증 없음',
      '지속시간(10초/연속)은 검증된 외부 rating 맵 또는 검증된 열모델이 있을 때만 판정', 'flux map은 셀 내부 쌍선형 보간, 외삽 없음; 온도 plane 사이 보간은 근거 선언 시에만',
      '회생 capability는 최소전류(에너지 회수) 정책 경계이며, 의도적 손실 증가 운전은 채택하지 않음', '안전 스크리닝(ASC/6SO, FTTI, DC-link)은 축약 모델이며 기능안전 승인이 아님',
      'MTPA 내부점 golden 값은 평탄한 목적함수 때문에 정확 해와 최대 5.5e-6 A 차이 (허용오차 1e-3 A 이내)'],
    en: ['Steady-state fundamental model only: PWM ripple, instantaneous peaks, SOA and transients are not evaluated', 'Verified against synthetic fixtures only: no hardware, supplier-data or external-simulator validation',
      'Duration (10 s / continuous) is decided only with a validated external rating envelope or validated thermal model', 'Flux maps: bilinear inside valid cells, no extrapolation; temperature planes interpolated only with a declared basis',
      'The regenerative capability is the minimum-current (energy-recovering) policy boundary; deliberate loss increase is never adopted', 'Safety screening (ASC/6SO, FTTI, DC link) is reduced-order and not a functional-safety approval',
      'Interior (MTPA) golden points differ from the exact optimum by up to 5.5e-6 A because the objective is flat there (within the 1e-3 A tolerance)'],
  };
  async function runVV() {
    const out = $('#vv-out'); busy(out, true);
    try {
      const a = await api('acceptance');
      out.innerHTML = `<div class="callout ${a.all_pass && a.manifest_ok ? 'good' : 'bad'}"><b>${a.all_pass && a.manifest_ok ? 'ALL PASS' : 'FAILURES'}</b> — ${a.rows.filter((r) => r.pass).length}/${a.rows.length} · manifest ${a.manifest_ok ? 'OK' : 'MISMATCH'} · ${fmt(a.elapsed_s, 3)} s<br><span class="tiny">${esc(a.scope)}</span></div>
        <div class="table-wrap"><table><thead><tr><th>group</th><th>case</th><th>metric</th><th class="num">value</th><th class="num">tolerance</th><th>labels</th><th>result</th></tr></thead><tbody>
        ${a.rows.map((r) => `<tr><td>${esc(r.group)}</td><td class="mono">${esc(r.case)}</td><td class="tiny">${esc(r.metric)}${r.certified ? ' · certified' : ''}</td><td class="num">${fmt(r.value, 3)}</td><td class="num">${fmt(r.tolerance, 3)}</td><td>${pill(r.labels_ok)}</td><td>${pill(r.pass)}</td></tr>`).join('')}</tbody></table></div>
        <h3>Manifest (SHA-256)</h3><div class="table-wrap"><table><thead><tr><th>file</th><th>sha256</th><th>ok</th></tr></thead><tbody>${a.manifest.map((m) => `<tr><td>${esc(m.name)}</td><td class="mono">${esc(m.sha256.slice(0, 16))}…</td><td>${pill(m.ok)}</td></tr>`).join('')}</tbody></table></div>`;
    } catch (e) { out.innerHTML = `<div class="callout bad">${esc(e.message)}</div>`; } finally { busy(out, false); }
  }
  function initVV() { $('#vv-run').onclick = runVV; }
  function renderLimits() { $('#vv-limits').innerHTML = (LIMITS[state.lang] || LIMITS.en).map((x) => `<li>${esc(x)}</li>`).join(''); }

  // ------------------------------------------------------------------ shell
  function setTab(name) {
    $$('.tabs [role=tab]').forEach((b) => b.setAttribute('aria-selected', String(b.dataset.tab === name)));
    $$('.page').forEach((p) => { p.hidden = p.dataset.page !== name; });
    if (name === 'decision' && !state.lastRecord && !state.autoRan) { state.autoRan = true; const b = $('.preset'); if (b) b.click(); }
    if (name === 'capability' && !$('#cap-chart svg')) runCapability();
    if (name === 'vv' && !$('#vv-out table')) runVV();
    if (name === 'safety' && !$('#ftti-out .kv')) runFtti();
    if (location.hash !== '#' + name) history.replaceState(null, '', '#' + name);
  }
  function applyTheme(th) {
    if (th) document.documentElement.setAttribute('data-theme', th); else document.documentElement.removeAttribute('data-theme');
  }
  function renderBadge(info) {
    const d = info.drive; const p = d.provenance;
    $('#drive-badge').innerHTML = `<span class="id">${esc(d.drive_id)}</span><span class="tag">${esc(d.fidelity)}</span><span class="tag">${esc(p.origin)}</span><span class="tag warn">${state.lang === 'ko' ? '하드웨어 미검증' : 'not hardware-validated'}</span>`;
    $('#drive-badge').title = p.validation_status;
    $('#version').textContent = 'v' + info.version;
  }
  async function boot() {
    applyTheme(store.get('twb.theme', ''));
    $('#theme').onclick = () => { const cur = document.documentElement.getAttribute('data-theme'); const dark = cur ? cur === 'dark' : matchMedia('(prefers-color-scheme: dark)').matches; const nxt = dark ? 'light' : 'dark'; applyTheme(nxt); store.set('twb.theme', nxt); };
    $('#lang').onclick = () => { state.lang = state.lang === 'ko' ? 'en' : 'ko'; store.set('twb.lang', state.lang); applyI18n(); renderLimits(); if (state.info) { renderBadge(state.info); initDecision(); } if (state.lastRecord) renderDecision(state.lastRecord, state.lastPayload); };
    applyI18n(); renderLimits();
    $$('.tabs [role=tab]').forEach((b) => b.addEventListener('click', () => setTab(b.dataset.tab)));
    try {
      state.info = await api('info');
      renderBadge(state.info);
      initDecision(); initCapability(); initDesign(); initSafety(); initThermal(); initVV();
      const tab = (location.hash || '#decision').slice(1);
      setTab(['decision', 'capability', 'design', 'safety', 'thermal', 'vv'].includes(tab) ? tab : 'decision');
    } catch (e) { toast(e.message, true); }
  }
  document.addEventListener('DOMContentLoaded', boot);
})();
