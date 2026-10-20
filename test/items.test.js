import { test } from 'node:test';
import assert from 'node:assert/strict';
import { withParent, prepareQueries, normalizeQueryItems, describeMetadata } from '../src/items.js';
import { parseAssets, parseXml } from '../src/xml.js';
import { AgilityClient } from '../src/client.js';
import { loadConfig } from '../src/config.js';

test('normalizes parent OID, name and number from projections and preserves embedded reference fields', () => {
  const result = parseAssets('<Asset id="Story:10"><Attribute name="Super.Name">Portfolio parent</Attribute><Attribute name="Super.Number">E-00001</Attribute><Relation name="Super"><Asset idref="Epic:1:7"><Attribute name="Name">Embedded name</Attribute></Asset></Relation></Asset>');
  const item = withParent(result.assets[0]);
  assert.equal(item.parentId, 'Epic:1');
  assert.equal(item.parentName, 'Portfolio parent');
  assert.equal(item.parent.number, 'E-00001');
  assert.equal(item.parentStatus, 'resolved');
  assert.equal(result.assets[0].relations.Super[0].attributes.Name, 'Embedded name');
});
test('distinguishes a root item from an unresolved parent or missing relationship data', () => {
  assert.equal(withParent({ oid: 'Epic:1', attributes: {}, relations: { Super: [] } }).parentStatus, 'none');
  assert.equal(withParent({ oid: 'Story:1', attributes: {}, relations: { Super: [{ oid: 'Epic:3' }] } }).parentStatus, 'unresolved');
  assert.equal(withParent({ oid: 'Story:1', attributes: {}, relations: {} }).parentStatus, 'unavailable');
  assert.equal(withParent({ oid: 'Story:1', attributes: {}, relations: { Super: [{ oid: 'invalid' }] } }).parentStatus, 'unavailable');
  const item = withParent({ oid: 'Story:1', attributes: { 'Parent.Name': 'Theme' }, relations: { Parent: [{ oid: 'Theme:9' }] } });
  assert.equal(item.parentId, null);
  assert.equal(item.parentStatus, 'unavailable');
});
test('adds parent selections to REST list/get/history despite narrow field selection', async () => {
  const config = await loadConfig({ baseUrl: 'https://example.test/v1' }, {});
  let count = 0;
  const client = new AgilityClient(config, { get: async () => ({ secret: 'synthetic' }) }, { fetchImpl: async url => {
    count++;
    assert.equal(url.searchParams.get('sel'), 'Description,Super,Super.Name,Super.Number');
    return new Response('<Assets total="1"><Asset id="Story:10"><Attribute name="Super.Name">Feature</Attribute><Relation name="Super"><Asset idref="Epic:1"/></Relation></Asset></Assets>');
  } });
  for (const result of [await client.list({ assetType: 'Story', select: ['Description'] }), await client.get({ oid: 'Story:10', select: ['Description'] }), await client.history({ oid: 'Story:10', select: ['Description'] })]) assert.equal(result.assets[0].parentName, 'Feature');
  assert.equal(count, 3);
});
test('enriches query.v1 items and nested workitems without inventing parent IDs from names', () => {
  const query = prepareQueries([{ from: 'Epic', select: ['Name', { from: 'Subs', select: ['Name'] }, { from: 'Owners', select: ['Name'] }] }])[0];
  assert.ok(query.select.includes('Super'));
  assert.ok(query.select[1].select.includes('Super.Name'));
  assert.deepEqual(query.select[2].select, ['Name']);
  const result = normalizeQueryItems([[{ _oid: 'Epic:1', Super: { _oid: 'Epic:2' }, 'Super.Name': 'Root', 'Super.Number': 'E-2', Subs: [{ _oid: 'Story:10', 'Super.ID': [{ _oid: 'Epic:1' }], 'Super.Name': 'Feature' }] }]]);
  assert.equal(result[0][0].parentId, 'Epic:2');
  assert.equal(result[0][0].Subs[0].parentName, 'Feature');
});
test('interprets singleton metadata definitions, related targets, reciprocals and flags', () => {
  const schema = describeMetadata(parseXml('<AssetType name="Story"><Base nameref="PrimaryWorkitem"/><AttributeDefinition name="Dependencies" attributetype="Relation" ismultivalue="True" isrequired="False"><RelatedAsset nameref="PrimaryWorkitem"/><ReciprocalRelation tokenref="PrimaryWorkitem.Dependants"/></AttributeDefinition><Operation name="Close"/></AssetType>'), 'Story');
  assert.equal(schema.fields.Dependencies.targetType, 'PrimaryWorkitem');
  assert.equal(schema.fields.Dependencies.reciprocal, 'Dependants');
  assert.equal(schema.fields.Dependencies.multiValue, true);
  assert.deepEqual(schema.operations, ['Close']);
});
test('query client injects parent selectors without replacing an explicit nested Super selection', async () => {
  const config = await loadConfig({ baseUrl: 'https://example.test/v1' }, {});
  const client = new AgilityClient(config, { get: async () => ({ secret: 'synthetic' }) }, { fetchImpl: async (url, options) => {
    const selected = JSON.parse(options.body)[0].select;
    assert.ok(selected.includes('Super.Name'));
    assert.equal(selected.filter(field => field === 'Super').length, 0);
    assert.equal(selected.filter(field => field?.from === 'Super').length, 1);
    return Response.json([[{ _oid: 'Story:10', Super: { _oid: 'Epic:1', Name: 'Feature' }, 'Super.Name': 'Feature' }]]);
  } });
  const result = await client.query({ queries: [{ from: 'Story', select: ['Name', { from: 'Super', select: ['Name'] }] }] });
  assert.equal(result[0][0].parentId, 'Epic:1');
  assert.equal(result[0][0].parentName, 'Feature');
});
test('post-write parent enrichment failure preserves mutation success and never repeats the write', async () => {
  const config = await loadConfig({ baseUrl: 'https://example.test/v1', enableWrites: true, retries: 0 }, {});
  let writes = 0;
  const client = new AgilityClient(config, { get: async () => ({ secret: 'synthetic' }) }, { fetchImpl: async (url, options) => {
    if (options.method === 'POST') { writes++; return new Response('<Asset id="Story:10:11"><Attribute name="Name">Created</Attribute></Asset>'); }
    return new Response('', { status: 403 });
  } });
  const result = await client.mutate({ assetType: 'Story', attributes: { Name: 'Created' } });
  assert.equal(writes, 1);
  assert.equal(result.assets[0].oid, 'Story:10:11');
  assert.equal(result.assets[0].parentStatus, 'unavailable');
  assert.match(result.hierarchyWarnings[0], /Mutation succeeded/);
});
