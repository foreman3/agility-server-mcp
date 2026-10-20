// Explicit read-only test against an operator-configured Agility tenant.
import assert from 'node:assert/strict';
import { loadConfig } from '../src/config.js';
import { AgilityClient } from '../src/client.js';
import { WindowsCredentialStore } from '../src/credentials.js';
import { oidPath } from '../src/xml.js';

if (process.env.AGILITY_LIVE_TEST !== '1') throw new Error('Set AGILITY_LIVE_TEST=1 to opt in to live tenant reads.');
if (process.platform !== 'win32') throw new Error('Live tenant validation requires the Windows user who owns the stored credential.');
const oid = process.env.AGILITY_LIVE_ITEM_OID;
if (!oid) throw new Error('Set AGILITY_LIVE_ITEM_OID to a visible current portfolio or backlog item.');
oidPath(oid, false);
const config = await loadConfig({ enableWrites: false, allowOperations: [] }, process.env);
const client = new AgilityClient(config, new WindowsCredentialStore());
const assetType = oid.split(':')[0];
const metadata = await client.metadata({ assetType });
assert.ok(metadata.AssetType, 'The tenant did not return asset type metadata.');
const read = await client.get({ oid, select: ['Name', 'Number'] });
const item = read.assets.find(asset => asset.oid === oid);
assert.ok(item, 'The item was not visible to the stored credential.');
assert.ok(['resolved', 'unresolved', 'none', 'unavailable'].includes(item.parentStatus), 'Missing parent coverage status.');
const detail = await client.detail({ oid, hierarchyDepth: 1, dependencyDepth: 1, pageSize: 20,
  maxNodes: 50, maxEdges: 100, maxRequests: 60, maxDurationMs: 30000 });
assert.equal(detail.item.oid, oid);
assert.ok(Array.isArray(detail.graph.edges));
assert.ok(Array.isArray(detail.impact.affected));
assert.ok(Array.isArray(detail.traversal.reasons));
if (process.env.AGILITY_LIVE_EXPECT_PARENT_OID) assert.equal(detail.item.parentId, process.env.AGILITY_LIVE_EXPECT_PARENT_OID);
if (process.env.AGILITY_LIVE_EXPECT_UPSTREAM_OID) {
  assert.ok(detail.dependencies.upstream.some(value => value.oid === process.env.AGILITY_LIVE_EXPECT_UPSTREAM_OID), 'Expected upstream dependency missing.');
}
if (process.env.AGILITY_LIVE_EXPECT_DOWNSTREAM_OID) {
  assert.ok(detail.dependencies.downstream.some(value => value.oid === process.env.AGILITY_LIVE_EXPECT_DOWNSTREAM_OID), 'Expected downstream dependency missing.');
}
if (process.env.AGILITY_LIVE_RELATION) {
  const related = await client.related({ oid, relation: process.env.AGILITY_LIVE_RELATION, pageSize: 20 });
  assert.ok(['ok', 'unsupported', 'unavailable'].includes(related.status));
  if (related.status === 'ok') assert.ok(Array.isArray(related.assets));
}
console.log(JSON.stringify({ validated: true, oid, parentStatus: detail.item.parentStatus,
  nodes: detail.graph.nodes.length, edges: detail.graph.edges.length,
  completeWithinRequestedDepth: detail.traversal.completeWithinRequestedDepth,
  reasons: detail.traversal.reasons }));
