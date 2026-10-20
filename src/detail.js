import { assertName, oidPath } from './xml.js';
import { canonicalOid, describeMetadata, itemSummary, withParent } from './items.js';

const scalarDefaults = ['Name', 'Number', 'Description', 'Reference', 'Estimate', 'ToDo', 'AssetState', 'CreateDate', 'ChangeDate', 'ChangeDateUTC', 'PlannedStart', 'PlannedEnd'];
const singleRelations = ['Scope', 'Status', 'Priority', 'Team', 'Timebox', 'Category', 'Parent', 'CreatedBy', 'ChangedBy'];
const list = value => [...new Set(value)];
class Limit extends Error { constructor(reason) { super(`Detailed item traversal stopped: ${reason}.`); this.reason = reason; } }
function checkInteger(value, min, max, label) {
  if (!Number.isInteger(value) || value < min || value > max) throw new Error(`${label} must be an integer between ${min} and ${max}.`);
}

// Per-operation schema cache: no stale metadata or cross-request cancellation coupling.
export function schemaReader(client) {
  const cache = new Map();
  const read = async (type, ancestry = []) => {
    assertName(type);
    if (ancestry.includes(type) || ancestry.length >= 12) throw new Error('Invalid or excessively deep metadata inheritance.');
    if (cache.has(type)) return cache.get(type);
    const promise = (async () => {
      const own = describeMetadata(await client.metadata({ assetType: type }), type);
      const base = own.baseType ? await read(own.baseType, [...ancestry, type]) : null;
      return { ...own, fields: { ...base?.fields, ...own.fields }, operations: list([...(base?.operations ?? []), ...own.operations]),
        inheritance: [type, ...(base?.inheritance ?? [])] };
    })();
    cache.set(type, promise);
    return promise;
  };
  return read;
}

function selectFor(schema, { detailed = false, select } = {}) {
  if (select) {
    for (const field of select) if (!schema.fields[field.split('.')[0]]) throw new Error(`Field ${field} is not defined for ${schema.assetType}.`);
    return list(['Name', ...select]);
  }
  const fields = detailed ? [...scalarDefaults, ...Object.values(schema.fields).filter(field => field.custom && !field.multiValue && !/relation|binary|blob|opaque|password/i.test(field.type ?? '')).map(field => field.name)] : ['Name', 'Number'];
  return list(fields.filter(field => schema.fields[field] && schema.fields[field].type !== 'Relation')
    .concat(detailed ? singleRelations.filter(field => schema.fields[field]?.type === 'Relation' && !schema.fields[field].multiValue).flatMap(field => [field, `${field}.Name`]) : [])).slice(0, 100);
}

async function relationPage(client, readSchema, { oid, relation, pageSize = 50, start = 0 }) {
  oidPath(oid, false); assertName(relation);
  const schema = await readSchema(oid.split(':')[0]);
  const definition = schema.fields[relation];
  if (!definition || definition.type !== 'Relation') return { oid, relation, status: 'unsupported', assets: [], nextStart: null, reason: 'Relationship is not defined by this instance metadata.' };
  if (!definition.targetType || !definition.reciprocal) return { oid, relation, status: 'unavailable', assets: [], nextStart: null, reason: 'Metadata does not supply a target type and reciprocal relationship for safe paging.' };
  const target = await readSchema(definition.targetType);
  const reciprocal = target.fields[definition.reciprocal];
  if (reciprocal?.type !== 'Relation' || reciprocal.targetType && !schema.inheritance.includes(reciprocal.targetType)) return { oid, relation, status: 'unavailable', assets: [], nextStart: null, reason: 'Reciprocal relationship cannot be verified in target metadata.' };
  // Query the reciprocal on the target collection so Agility pages related assets,
  // rather than returning an unbounded multi-value projection on the source asset.
  const result = await client.list({ assetType: definition.targetType, select: selectFor(target),
    where: `${definition.reciprocal}='${oid}'`, sort: ['ID'], pageSize, start });
  return { oid, relation, status: 'ok', ...result };
}
function continuation(oid, relation, pageSize, start) {
  return { tool: 'agility_related_items', arguments: { oid, relation, pageSize, start } };
}

export async function relatedItems(client, { oid, relation, pageSize = 50, start = 0, signal }) {
  checkInteger(pageSize, 1, 200, 'pageSize'); checkInteger(start, 0, Number.MAX_SAFE_INTEGER, 'start');
  const scoped = Object.create(client);
  scoped.request = (path, options) => client.request(path, { ...options, signal });
  const source = await scoped.get({ oid, select: ['Name'] });
  if (!source.assets.some(asset => canonicalOid(asset.oid) === oid)) throw new Error('The requested source item was not returned by Agility.');
  const result = await relationPage(scoped, schemaReader(scoped), { oid, relation, pageSize, start });
  return { ...result, permissionScope: 'visible_to_current_user', continuation: result.nextStart === null ? null : continuation(oid, relation, pageSize, result.nextStart) };
}

function cycles(nodes, edges, kind) {
  const adjacency = new Map(nodes.map(node => [node.oid, []]));
  for (const edge of edges) if (edge.kind === kind) adjacency.get(edge.from)?.push(edge.to);
  const state = new Map(), stack = [], found = [], signatures = new Set();
  function visit(oid) {
    state.set(oid, 1); stack.push(oid);
    for (const next of adjacency.get(oid) ?? []) {
      if (state.get(next) === 1) {
        const path = stack.slice(stack.indexOf(next));
        const key = [...path].sort().join('|');
        if (!signatures.has(key) && found.length < 25) { signatures.add(key); found.push({ kind, path: [...path, next] }); }
      } else if (!state.has(next)) visit(next);
    }
    stack.pop(); state.set(oid, 2);
  }
  for (const node of nodes) if (!state.has(node.oid)) visit(node.oid);
  return found;
}

// Paths are witnesses made entirely of returned explicit dependency edges.
export function dependencyImpact(root, edges) {
  const forward = new Map(), backward = new Map();
  for (const { from, to, kind } of edges) {
    if (kind !== 'dependency') continue;
    if (!forward.has(from)) forward.set(from, new Set());
    if (!backward.has(to)) backward.set(to, new Set());
    forward.get(from).add(to);
    backward.get(to).add(from);
  }
  function paths(adjacency, reverse = false) {
    const found = new Map([[root, [root]]]), queue = [root];
    for (const current of queue) for (const next of [...(adjacency.get(current) ?? [])].sort()) {
      if (found.has(next)) continue;
      const path = reverse ? [next, ...found.get(current)] : [...found.get(current), next];
      found.set(next, path);
      queue.push(next);
    }
    found.delete(root);
    return [...found].map(([oid, path]) => ({ oid, distance: path.length - 1, path }))
      .sort((a, b) => a.distance - b.distance || a.oid.localeCompare(b.oid));
  }
  const affected = paths(forward);
  const prerequisites = paths(backward, true);
  return { semantics: 'Explicit dependency edges only; paths are evidence in graph.edges.',
    affected, blockingChains: prerequisites, directDependants: affected.filter(value => value.distance === 1).map(value => value.oid) };
}

export async function getItemDetail(client, { oid, hierarchyDepth = 2, dependencyDepth = 1, pageSize = 50,
  maxNodes = 100, maxEdges = 500, maxRequests = 80, maxDurationMs = 30000, includeMetadata = true, select, signal } = {}) {
  oidPath(oid, false);
  for (const [value, min, max, label] of [[hierarchyDepth, 0, 10, 'hierarchyDepth'], [dependencyDepth, 0, 10, 'dependencyDepth'], [pageSize, 1, 200, 'pageSize'],
    [maxNodes, 1, 500, 'maxNodes'], [maxEdges, 1, 5000, 'maxEdges'], [maxRequests, 5, 300, 'maxRequests'], [maxDurationMs, 1000, 120000, 'maxDurationMs']]) checkInteger(value, min, max, label);
  const started = Date.now();
  const deadline = AbortSignal.timeout(maxDurationMs);
  const combined = signal ? AbortSignal.any([signal, deadline]) : deadline;
  let requests = 0;
  const scoped = Object.create(client);
  scoped.request = (path, options = {}) => client.request(path, { ...options, signal: combined, beforeAttempt: () => {
    if (requests >= maxRequests) throw new Limit('maxRequests');
    requests++;
  } });
  const readSchema = schemaReader(scoped);
  const warnings = [], reasons = new Set();
  let schema;
  try { schema = await readSchema(oid.split(':')[0]); }
  catch (error) {
    if (signal?.aborted || deadline.aborted || error instanceof Limit) throw error;
    warnings.push({ code: 'metadata_unavailable', message: error.message });
  }
  const rootResult = await scoped.get({ oid, select: schema ? selectFor(schema, { detailed: true, select }) : select ?? ['Name', 'Number', 'Description'] });
  const item = rootResult.assets.find(asset => canonicalOid(asset.oid) === oid);
  if (!item) throw new Error('The requested item was not returned by Agility.');
  if (item.parentStatus === 'unavailable') reasons.add('parent_unavailable');
  const nodes = new Map([[oid, itemSummary(item)]]), edges = new Map(), coverage = new Map(), queue = [];
  const outputLimit = client.config.maxResponseBytes;
  const directions = [
    { key: 'ancestors', candidates: ['Super'], kind: 'hierarchy', reverse: true, depth: hierarchyDepth },
    { key: 'descendants', candidates: ['Subs'], kind: 'hierarchy', reverse: false, depth: hierarchyDepth },
    { key: 'upstream', candidates: ['Dependencies', 'UpstreamDependencies'], kind: 'dependency', reverse: true, depth: dependencyDepth },
    { key: 'downstream', candidates: ['Dependants', 'Dependents', 'DownstreamDependencies'], kind: 'dependency', reverse: false, depth: dependencyDepth },
    { key: 'blockingIssues', candidates: ['BlockingIssues'], kind: 'blocker', reverse: true, depth: 1 },
  ];
  const reached = new Map(directions.map(direction => [direction.key, new Map()])) ;
  const expanded = new Set();
  const pageSeen = new Map();
  let omitMetadataForSize = false, omittedWarnings = 0;
  function resultData(extraPending = []) {
    const pending = [...extraPending, ...queue].map(task => ({ oid: task.source, direction: task.direction.key,
      relation: coverage.get(`${task.direction.key}|${task.source}`)?.relation ?? task.direction.candidates[0], start: task.start }));
    const group = key => [...reached.get(key)].map(([target, distance]) => ({ ...nodes.get(target), distance }));
    const nodeList = [...nodes.values()], edgeList = [...edges.values()];
    const graphCycles = [...cycles(nodeList, edgeList, 'hierarchy'), ...cycles(nodeList, edgeList, 'dependency')];
    return { item, metadata: includeMetadata && !omitMetadataForSize ? schema ?? null : undefined,
      metadataStatus: omitMetadataForSize ? 'omitted_for_size' : schema ? 'available' : 'unavailable',
      hierarchy: { parent: item.parent ?? null, ancestors: group('ancestors'), descendants: group('descendants') },
      dependencies: { semantics: 'Edges point from prerequisite to dependent; upstream items must finish before this item, downstream items depend on it.', scope: 'explicit_relationships; implicit inherited constraints are not synthesized', upstream: group('upstream'), downstream: group('downstream'), blockingIssues: group('blockingIssues') },
      impact: { ...dependencyImpact(oid, edgeList), cycles: graphCycles.filter(cycle => cycle.kind === 'dependency'),
        completeWithinRequestedDepth: reasons.size === 0 && pending.length === 0 },
      graph: { nodes: nodeList, edges: edgeList, cycles: graphCycles },
      coverage: [...coverage.values()], warnings,
      ...(omittedWarnings ? { warningsOmitted: omittedWarnings } : {}),
      traversal: { completeWithinRequestedDepth: reasons.size === 0 && pending.length === 0, truncated: reasons.size > 0 || pending.length > 0,
        reasons: [...reasons], pending, requests, elapsedMs: Date.now() - started, permissionScope: 'visible_to_current_user',
        limits: { hierarchyDepth, dependencyDepth, pageSize, maxNodes, maxEdges, maxRequests, maxDurationMs },
        consistency: 'live_nontransactional; concurrent changes and account visibility can affect results',
        depthBoundaries: directions.filter(direction => direction.key !== 'blockingIssues').map(direction => ({ direction: direction.key, depth: direction.depth,
          unexpanded: direction.depth === 0 ? [oid] : [...reached.get(direction.key)].filter(([, distance]) => distance === direction.depth).map(([target]) => target) })) },
    };
  }
  const resultBytes = extraPending => Buffer.byteLength(JSON.stringify(resultData(extraPending)));
  function enqueue(source, direction, depth, start = 0) {
    const key = `${direction.key}|${source}|${start}`;
    if (expanded.has(key)) return;
    expanded.add(key); queue.push({ source, direction, depth, start });
  }
  if (schema) {
    for (const direction of directions) if (direction.depth) enqueue(oid, direction, 1);
  } else reasons.add('metadata_unavailable');
  if (resultBytes() + 1024 > outputLimit && includeMetadata) {
    omitMetadataForSize = true;
    reasons.add('maxResponseBytes');
  }
  if (resultBytes() + 1024 > outputLimit) throw new Error('Detail root exceeds maxResponseBytes. Reduce selection or increase the configured limit.');
  while (queue.length) {
    if (signal?.aborted) throw new Error('Agility request cancelled.');
    // Finish shallower relationship pages before expanding deeper nodes, so distances
    // stay shortest-path distances even when a direct neighbor occurs on a later page.
    queue.sort((a, b) => a.depth - b.depth);
    const task = queue.shift();
    const { source, direction, depth, start } = task;
    const key = `${direction.key}|${source}`;
    let relation = direction.candidates[0];
    try {
      if (deadline.aborted) throw new Limit('maxDurationMs');
      const sourceSchema = await readSchema(source.split(':')[0]);
      relation = direction.candidates.find(candidate => sourceSchema.fields[candidate]?.type === 'Relation') ?? relation;
      const result = await relationPage(scoped, readSchema, { oid: source, relation, pageSize, start });
      const existingRecord = coverage.has(key);
      const record = coverage.get(key) ?? { oid: source, direction: direction.key, relation, fetched: 0, status: 'complete', nextStart: null };
      coverage.set(key, record);
      if (!existingRecord && resultBytes([task]) + 1024 > outputLimit) {
        coverage.delete(key);
        queue.unshift(task);
        throw new Limit('maxResponseBytes');
      }
      if (result.status !== 'ok') {
        record.status = result.status; record.reason = result.reason;
        if (result.status === 'unavailable') reasons.add('relationship_unavailable');
        continue;
      }
      let accepted = 0;
      const seen = pageSeen.get(key) ?? new Set();
      pageSeen.set(key, seen);
      for (const asset of result.assets) {
        const target = canonicalOid(asset.oid);
        if (!target) { reasons.add('invalid_related_oid'); accepted++; continue; }
        if (seen.has(target)) {
          reasons.add('pagination_changed'); accepted++;
          warnings.push({ code: 'pagination_changed', oid: source, relation, message: 'A related item repeated across pages; the collection may have changed during traversal.' });
          continue;
        }
        const summary = itemSummary(withParent(asset));
        const edge = { from: direction.reverse ? target : source, to: direction.reverse ? source : target, kind: direction.kind };
        const edgeKey = `${edge.kind}|${edge.from}|${edge.to}`;
        let reason = !nodes.has(target) && nodes.size >= maxNodes ? 'maxNodes' : !edges.has(edgeKey) && edges.size >= maxEdges ? 'maxEdges' : null;
        if (reason) {
          reasons.add(reason); record.status = 'partial'; record.nextStart = start + accepted;
          queue.unshift({ ...task, start: record.nextStart });
          throw new Limit(reason);
        }
        const newNode = !nodes.has(target), newEdge = !edges.has(edgeKey);
        if (newNode) nodes.set(target, summary);
        if (newEdge) edges.set(edgeKey, edge);
        let newReach = false;
        if (target !== oid && !reached.get(direction.key).has(target)) {
          reached.get(direction.key).set(target, depth);
          newReach = true;
        }
        record.fetched++;
        if (resultBytes([{ ...task, start: start + accepted }]) + 1024 > outputLimit) {
          record.fetched--;
          if (newReach) reached.get(direction.key).delete(target);
          if (newEdge) edges.delete(edgeKey);
          if (newNode) nodes.delete(target);
          reasons.add('maxResponseBytes'); record.status = 'partial'; record.nextStart = start + accepted;
          queue.unshift({ ...task, start: record.nextStart });
          throw new Limit('maxResponseBytes');
        }
        seen.add(target);
        accepted++;
        if (summary.parentStatus === 'unavailable') reasons.add('parent_unavailable');
        if (newReach && depth < direction.depth && direction.key !== 'blockingIssues') enqueue(target, direction, depth + 1);
      }
      if (record.total !== undefined && result.total !== undefined && record.total !== result.total) reasons.add('pagination_changed');
      record.total = result.total;
      record.nextStart = result.nextStart;
      record.status = result.nextStart === null ? 'complete' : 'partial';
      if (record.nextStart !== null) record.continuation = continuation(source, relation, pageSize, record.nextStart);
      if (result.paginationStatus === 'stalled') {
        reasons.add('pagination_stalled'); record.status = 'partial'; record.reason = result.paginationWarning;
      }
      if (result.nextStart !== null) {
        if (result.nextStart <= start || !result.assets.length) { reasons.add('pagination_stalled'); record.status = 'partial'; }
        else enqueue(source, direction, depth, result.nextStart);
      }
    } catch (error) {
      if (signal?.aborted) throw new Error('Agility request cancelled.');
      if (error instanceof Limit || deadline.aborted) {
        reasons.add(deadline.aborted ? 'maxDurationMs' : error.reason);
        if (!queue.some(value => value.source === source && value.direction.key === direction.key)) queue.unshift(task);
        break;
      }
      coverage.set(key, { oid: source, direction: direction.key, relation, status: 'error', start, message: error.message });
      warnings.push({ code: 'relationship_error', oid: source, relation, message: error.message });
      reasons.add('relationship_error');
    }
  }
  for (const record of coverage.values()) if (record.nextStart !== null && record.nextStart !== undefined) record.continuation = continuation(record.oid, record.relation, pageSize, record.nextStart);
  if (resultBytes() > outputLimit && includeMetadata) {
    omitMetadataForSize = true;
    reasons.add('maxResponseBytes');
  }
  while (warnings.length && resultBytes() > outputLimit) {
    warnings.pop(); omittedWarnings++; reasons.add('maxResponseBytes');
  }
  if (resultBytes() > outputLimit) throw new Error('Detail root or traversal bookkeeping exceeds maxResponseBytes. Reduce selection or increase the configured limit.');
  return resultData();
}
