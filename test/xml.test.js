import { test } from 'node:test';
import assert from 'node:assert/strict';
import { assetXml, oidPath, parseAssets, parseXml } from '../src/xml.js';

export const collectionXml = '<Assets total="2"><Asset id="Story:1:4" href="/v1/Data/Story/1/4"><Attribute name="Name">A &amp; B</Attribute><Attribute name="Estimate">3</Attribute><Relation name="Owners"><Asset idref="Member:20"/></Relation></Asset><Asset id="Story:2"><Attribute name="Name">Second</Attribute></Asset></Assets>';

test('preserves string values and normalizes XML collections, empty assets, relations and multivalues', () => {
  const result = parseAssets(collectionXml);
  assert.equal(result.total, 2);
  assert.equal(result.assets[0].attributes.Name, 'A & B');
  assert.equal(result.assets[0].attributes.Estimate, '3');
  assert.equal(result.assets[0].relations.Owners[0].oid, 'Member:20');
  assert.deepEqual(parseAssets('<Assets total="0"/>'), { assets: [], total: 0 });
  assert.deepEqual(parseAssets('<Assets/>'), { assets: [] });
  assert.equal(parseAssets('<Asset id="Story:1"><Attribute name="Tag"><Value>A</Value><Value>B</Value></Attribute></Asset>').assets[0].attributes.Tag.length, 2);
  assert.throws(() => parseAssets('<html/>'));
});
test('escapes scalar XML and encodes relationship actions exactly as required by Agility', () => {
  const xml = assetXml({ Name: '<hello> & "quotes"', Estimate: 0, Description: null }, [
    { name: 'Scope', action: 'set', targets: ['Scope:0'] },
    { name: 'Owners', action: 'add', targets: ['Member:20'] },
    { name: 'Owner', action: 'set', targets: [] },
  ]);
  assert.match(xml, /&lt;hello&gt; &amp; &quot;quotes&quot;/);
  assert.match(xml, /<Relation name="Scope" act="set"><Asset idref="Scope:0"\/><\/Relation>/);
  assert.match(xml, /<Relation name="Owners"><Asset idref="Member:20" act="add"\/><\/Relation>/);
  assert.match(xml, /<Relation name="Owner" act="set"><\/Relation>/);
  assert.doesNotThrow(() => parseXml(xml));
});
test('rejects XML injection, invalid OIDs, invalid relationship cardinality and DTDs', () => {
  assert.throws(() => assetXml({ 'Name" bad="x': 'value' }));
  assert.throws(() => assetXml({ Name: '\x01' }));
  assert.throws(() => assetXml({ Name: {} }));
  assert.throws(() => assetXml({}, []));
  assert.throws(() => assetXml({}, [{ name: 'Scope', action: 'set', targets: ['Scope:1', 'Scope:2'] }]));
  for (const oid of ['../Story:1', 'Story:1?op=Delete', 'Story:1/../../Member:2']) assert.throws(() => oidPath(oid));
  assert.throws(() => oidPath('Story:1:2', false));
  assert.throws(() => parseXml('<!DOCTYPE x [<!ENTITY x "secret">]><Asset>&x;</Asset>'));
  assert.throws(() => parseXml('<Asset>'));
});
