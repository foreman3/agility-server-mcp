import { test } from 'node:test';
import assert from 'node:assert/strict';
import { loadConfig } from '../src/config.js';
import { AgilityClient } from '../src/client.js';
import { schemaReader } from '../src/detail.js';

const escape = value => String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('"', '&quot;');
const scalar = (name, type = 'Text', custom = false) => `<AttributeDefinition name="${name}" attributetype="${type}" iscustom="${custom}"/>`;
const relation = (name, target, reciprocal, multi = true) => `<AttributeDefinition name="${name}" attributetype="Relation" ismultivalue="${multi}"><RelatedAsset nameref="${target}"/><ReciprocalRelation tokenref="${target}.${reciprocal}"/></AttributeDefinition>`;
const metadata = {
  BaseAsset: `${scalar('Name')}${scalar('Description')}`,
  Workitem: `<Base nameref="BaseAsset"/>${scalar('Number')}${relation('Super', 'Epic', 'Subs', false)}`,
  Epic: `<Base nameref="Workitem"/>${relation('Subs', 'Workitem', 'Super')}${relation('Dependencies', 'Epic', 'Dependants')}${relation('Dependants', 'Epic', 'Dependencies')}`,
  PrimaryWorkitem: `<Base nameref="Workitem"/>${scalar('Estimate', 'Number')}${relation('Dependencies', 'PrimaryWorkitem', 'Dependants')}${relation('Dependants', 'PrimaryWorkitem', 'Dependencies')}${relation('BlockingIssues', 'Issue', 'BlockedPrimaryWorkitems')}`,
  Story: `<Base nameref="PrimaryWorkitem"/>${scalar('Custom_Impact', 'Text', true)}<Operation name="Close"/>`,
  Defect: '<Base nameref="PrimaryWorkitem"/>',
  Issue: `<Base nameref="BaseAsset"/>${scalar('Number')}${relation('BlockedPrimaryWorkitems', 'PrimaryWorkitem', 'BlockingIssues')}`,
};
const baseData = () => ({
  'Epic:1': { Name: 'Root initiative', Number: 'E-1', relations: {} },
  'Epic:2': { Name: 'Feature', Number: 'E-2', relations: { Super: ['Epic:1'] } },
  'Story:10': { Name: 'Implement UI', Number: 'S-10', Description: 'Detailed description', Custom_Impact: 'High', Estimate: '5', relations: { Super: ['Epic:2'], Dependencies: ['Story:20', 'Defect:30'], BlockingIssues: ['Issue:5'] } },
  'Story:20': { Name: 'Build API', Number: 'S-20', relations: { Super: ['Epic:2'], Dependencies: ['Story:50'] } },
  'Defect:30': { Name: 'Fix auth', Number: 'D-30', relations: { Super: ['Epic:2'] } },
  'Story:40': { Name: 'Integration', Number: 'S-40', relations: { Dependencies: ['Story:10'] } },
  'Story:50': { Name: 'Infra', Number: 'S-50', relations: { Dependencies: ['Story:10'] } },
  'Issue:5': { Name: 'Access blocker', Number: 'I-5', relations: {} },
});
function xmlAsset(oid, data, selected, all) {
  const row = data[oid];
  const content = selected.map(field => {
    if (field === 'Super') return `<Relation name="Super">${(row.relations.Super ?? []).map(parent => `<Asset idref="${parent}"><Attribute name="Name">${escape(all[parent]?.Name ?? '')}</Attribute></Asset>`).join('')}</Relation>`;
    if (field.startsWith('Super.')) return `<Attribute name="${field}">${escape(all[row.relations.Super?.[0]]?.[field.slice(6)] ?? '')}</Attribute>`;
    return `<Attribute name="${field}">${escape(row[field] ?? '')}</Attribute>`;
  }).join('');
  return `<Asset id="${oid}">${content}</Asset>`;
}
async function fixture({ data = baseData(), schemas = metadata, fail, wait, overrides = {} } = {}) {
  // Derive the reciprocal relationships using actual graph edges.
  for (const [oid, row] of Object.entries(data)) {
    for (const target of row.relations.Super ?? []) if (data[target]) (data[target].relations.Subs ??= []).push(oid);
    for (const target of row.relations.Dependencies ?? []) if (data[target]) (data[target].relations.Dependants ??= []).push(oid);
    for (const target of row.relations.BlockingIssues ?? []) if (data[target]) (data[target].relations.BlockedPrimaryWorkitems ??= []).push(oid);
  }
  const requests = [];
  const config = await loadConfig({ baseUrl: 'https://example.test/v1', retries: 0, ...overrides }, {});
  const client = new AgilityClient(config, { get: async () => ({ secret: 'synthetic' }) }, { fetchImpl: async (url, options) => {
    requests.push(url);
    await wait?.(url, options);
    const failure = fail?.(url);
    if (failure) return new Response('', { status: failure });
    if (url.pathname.includes('/meta.v1/')) {
      const type = url.pathname.split('/').at(-1);
      return schemas[type] === undefined ? new Response('', { status: 404 }) : new Response(`<AssetType name="${type}" version="test">${schemas[type]}</AssetType>`);
    }
    const parts = url.pathname.split('/');
    const type = parts[4];
    const id = parts[5];
    const selected = (url.searchParams.get('sel') ?? 'Name').split(',');
    if (id) {
      const oid = `${type}:${id}`;
      return data[oid] ? new Response(xmlAsset(oid, data, selected, data)) : new Response('', { status: 404 });
    }
    const where = url.searchParams.get('where');
    const match = /^([A-Za-z][A-Za-z0-9_]*)='([A-Za-z][A-Za-z0-9_]*:\d+)'$/.exec(where);
    assert.ok(match, `Expected safe reciprocal filter, got ${where}`);
    const matches = Object.entries(data).filter(([oid, row]) => {
      const concrete = oid.split(':')[0];
      const valid = concrete === type || type === 'Workitem' && ['Epic', 'Story', 'Defect'].includes(concrete) || type === 'PrimaryWorkitem' && ['Story', 'Defect'].includes(concrete);
      return valid && (row.relations[match[1]] ?? []).includes(match[2]);
    });
    const [size, start] = url.searchParams.get('page').split(',').map(Number);
    return new Response(`<Assets total="${matches.length}">${matches.slice(start, start + size).map(([oid]) => xmlAsset(oid, data, selected, data)).join('')}</Assets>`);
  } });
  return { client, requests };
}

test('detailed item includes parent, custom fields, inherited metadata and both dependency directions with correct edge orientation', async () => {
  const { client } = await fixture();
  const result = await client.detail({ oid: 'Story:10', pageSize: 1 });
  assert.equal(result.item.parentId, 'Epic:2');
  assert.equal(result.item.parentName, 'Feature');
  assert.equal(result.item.attributes.Description, 'Detailed description');
  assert.equal(result.item.attributes.Custom_Impact, 'High');
  assert.equal(result.metadata.fields.Dependencies.targetType, 'PrimaryWorkitem');
  assert.deepEqual(result.metadata.inheritance, ['Story', 'PrimaryWorkitem', 'Workitem', 'BaseAsset']);
  assert.deepEqual(result.hierarchy.ancestors.map(value => value.oid), ['Epic:2', 'Epic:1']);
  assert.deepEqual(result.dependencies.upstream.map(value => value.oid), ['Story:20', 'Defect:30']);
  assert.deepEqual(result.dependencies.downstream.map(value => value.oid), ['Story:40', 'Story:50']);
  assert.equal(result.dependencies.blockingIssues[0].name, 'Access blocker');
  assert.deepEqual(result.impact.directDependants, ['Story:40', 'Story:50']);
  assert.deepEqual(result.impact.affected.find(value => value.oid === 'Story:40').path, ['Story:10', 'Story:40']);
  assert.deepEqual(result.impact.blockingChains.find(value => value.oid === 'Story:20').path, ['Story:20', 'Story:10']);
  assert.ok(result.graph.edges.some(edge => edge.kind === 'dependency' && edge.from === 'Story:20' && edge.to === 'Story:10'));
  assert.ok(result.graph.edges.some(edge => edge.kind === 'dependency' && edge.from === 'Story:10' && edge.to === 'Story:40'));
  assert.equal(result.traversal.completeWithinRequestedDepth, true);
  assert.ok(result.traversal.depthBoundaries.find(value => value.direction === 'upstream').unexpanded.includes('Story:20'));
  assert.equal(result.coverage.find(value => value.direction === 'upstream' && value.oid === 'Story:10').fetched, 2);
});
test('portfolio descendants and transitive dependency graph terminate on cycles', async () => {
  const { client } = await fixture();
  const epic = await client.detail({ oid: 'Epic:1', hierarchyDepth: 2 });
  assert.equal(epic.item.parentStatus, 'none');
  assert.ok(epic.hierarchy.descendants.some(value => value.oid === 'Story:10' && value.distance === 2 && value.parentId === 'Epic:2'));
  const result = await client.detail({ oid: 'Story:10', dependencyDepth: 5 });
  assert.equal(new Set(result.graph.nodes.map(value => value.oid)).size, result.graph.nodes.length);
  assert.ok(result.graph.cycles.some(cycle => cycle.kind === 'dependency' && cycle.path.includes('Story:50') && cycle.path[0] === cycle.path.at(-1)));
  assert.ok(result.impact.cycles.some(cycle => cycle.path.includes('Story:50')));
  assert.ok(result.traversal.requests < 80);
});
test('a shared dependency diamond is not reported as a cycle', async () => {
  const data = baseData();
  data['Story:50'].relations.Dependencies = [];
  data['Defect:30'].relations.Dependencies = ['Story:50'];
  const { client } = await fixture({ data });
  const result = await client.detail({ oid: 'Story:10', dependencyDepth: 4 });
  assert.deepEqual(result.graph.cycles, []);
  assert.equal(result.dependencies.upstream.find(value => value.oid === 'Story:50').distance, 2);
});
test('missing reciprocal metadata is reported unavailable rather than silently omitting dependencies', async () => {
  const { client } = await fixture({ schemas: { ...metadata, PrimaryWorkitem: '<Base nameref="Workitem"/><AttributeDefinition name="Dependencies" attributetype="Relation"/>' } });
  const result = await client.detail({ oid: 'Story:10' });
  assert.equal(result.coverage.find(value => value.direction === 'upstream').status, 'unavailable');
  assert.ok(result.traversal.reasons.includes('relationship_unavailable'));
});
test('elapsed-time budget cancels an in-flight related query and preserves partial data', { timeout: 5000 }, async () => {
  const { client } = await fixture({ wait: async (url, options) => {
    if (url.searchParams.get('where') !== "Dependants='Story:10'") return;
    await new Promise((resolve, reject) => {
      const timer = setTimeout(resolve, 3000);
      options.signal.addEventListener('abort', () => { clearTimeout(timer); reject(options.signal.reason); }, { once: true });
    });
  } });
  const result = await client.detail({ oid: 'Story:10', maxDurationMs: 1000 });
  assert.equal(result.item.parentName, 'Feature');
  assert.ok(result.traversal.reasons.includes('maxDurationMs'));
  assert.ok(result.traversal.pending.length);
});
test('node and edge caps produce explicit truncation and a resumable page', async () => {
  for (const options of [{ maxNodes: 2 }, { maxEdges: 1 }]) {
    const { client } = await fixture();
    const result = await client.detail({ oid: 'Story:10', ...options });
    assert.equal(result.traversal.truncated, true);
    assert.ok(result.traversal.pending.length);
    assert.ok(result.graph.nodes.length <= (options.maxNodes ?? 100));
    assert.ok(result.graph.edges.length <= (options.maxEdges ?? 500));
    assert.ok(result.coverage.some(value => value.continuation?.tool === 'agility_related_items'));
  }
});
test('the complete detailed response stays within its byte budget and retains continuation data', async () => {
  const limit = 6500;
  const { client } = await fixture({ overrides: { maxResponseBytes: limit } });
  const result = await client.detail({ oid: 'Story:10', pageSize: 1, dependencyDepth: 3 });
  assert.ok(Buffer.byteLength(JSON.stringify(result)) <= limit);
  assert.equal(result.traversal.truncated, true);
  assert.ok(result.traversal.reasons.includes('maxResponseBytes'));
  assert.ok(result.traversal.pending.length);
});
test('request cap counts metadata and API attempts and retains already loaded item', async () => {
  const { client, requests } = await fixture();
  const result = await client.detail({ oid: 'Story:10', maxRequests: 5 });
  assert.equal(result.item.attributes.Name, 'Implement UI');
  assert.equal(result.traversal.requests, 5);
  assert.equal(requests.length, 5);
  assert.deepEqual(result.traversal.reasons, ['maxRequests']);
});
test('relationship permission failures remain distinct from empty/unsupported relationships', async () => {
  const { client } = await fixture({ fail: url => url.searchParams.get('where') === "Dependencies='Story:10'" ? 403 : null });
  const result = await client.detail({ oid: 'Story:10' });
  assert.equal(result.traversal.completeWithinRequestedDepth, false);
  assert.ok(result.coverage.some(value => value.direction === 'downstream' && value.status === 'error' && /403/.test(value.message)));
  assert.ok(result.coverage.some(value => value.direction === 'descendants' && value.status === 'unsupported'));
  assert.equal(result.dependencies.upstream.length, 2);
});
test('missing metadata returns item with explicit incomplete coverage, not empty dependencies presented as complete', async () => {
  const { client } = await fixture({ fail: url => url.pathname.includes('/meta.v1/') ? 403 : null });
  const result = await client.detail({ oid: 'Story:10' });
  assert.equal(result.item.parentId, 'Epic:2');
  assert.equal(result.metadataStatus, 'unavailable');
  assert.equal(result.traversal.completeWithinRequestedDepth, false);
  assert.ok(result.traversal.reasons.includes('metadata_unavailable'));
});
test('paged relationship tool returns IDs/names, stable paging and correct continuation arguments', async () => {
  const { client } = await fixture();
  const first = await client.related({ oid: 'Story:10', relation: 'Dependencies', pageSize: 1 });
  assert.equal(first.assets[0].attributes.Name, 'Build API');
  assert.equal(first.assets[0].parentName, 'Feature');
  assert.deepEqual(first.continuation.arguments, { oid: 'Story:10', relation: 'Dependencies', pageSize: 1, start: 1 });
  const second = await client.related(first.continuation.arguments);
  assert.equal(second.assets[0].oid, 'Defect:30');
  assert.equal(second.continuation, null);
  assert.equal((await client.related({ oid: 'Story:10', relation: 'Subs' })).status, 'unsupported');
  await assert.rejects(client.related({ oid: 'Story:999', relation: 'Dependencies' }), /404/);
});
test('rejects invalid relationship targets, cyclic inheritance, historical detail and out-of-range limits', async () => {
  const bad = await fixture({ schemas: { ...metadata, Story: '<Base nameref="Story"/>' } });
  await assert.rejects(schemaReader(bad.client)('Story'), /inheritance/);
  const { client } = await fixture();
  await assert.rejects(client.detail({ oid: 'Story:10:5' }), /without a moment/);
  await assert.rejects(client.detail({ oid: 'Story:10', maxNodes: 0 }), /maxNodes/);
  await assert.rejects(client.related({ oid: 'Story:10', relation: 'Dependencies?op=Delete' }), /Invalid/);
  const controller = new AbortController(); controller.abort();
  await assert.rejects(client.detail({ oid: 'Story:10', signal: controller.signal }));
});
