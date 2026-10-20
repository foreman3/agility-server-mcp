import { XMLParser, XMLValidator } from 'fast-xml-parser';

export const namePattern = /^[A-Za-z][A-Za-z0-9_]*$/;
export const oidPattern = /^[A-Za-z][A-Za-z0-9_]*:\d+(?::\d+)?$/;
export function assertName(value) {
  if (typeof value !== 'string' || !namePattern.test(value)) throw new Error('Invalid asset, attribute, relation, or operation name.');
  return value;
}
export function oidPath(oid, allowMoment = true) {
  if (typeof oid !== 'string' || !oidPattern.test(oid) || (!allowMoment && oid.split(':').length !== 2)) throw new Error('Expected an OID such as Story:1234; mutations require an OID without a moment.');
  return oid.split(':').join('/');
}
function escapeXml(value) {
  const text = value === null ? '' : String(value);
  if (/[\x00-\x08\x0b\x0c\x0e-\x1f\uFFFE\uFFFF]/u.test(text) || !text.isWellFormed()) throw new Error('Value contains invalid XML characters.');
  return text.replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&apos;' })[c]);
}
export function assetXml(attributes = {}, relations = []) {
  const scalar = Object.entries(attributes).map(([name, value]) => {
    assertName(name);
    if (value !== null && !['string', 'number', 'boolean'].includes(typeof value)) throw new Error('Attributes must be scalar values or null.');
    if (typeof value === 'number' && !Number.isFinite(value)) throw new Error('Attribute numbers must be finite.');
    return `<Attribute name="${name}" act="set">${escapeXml(value)}</Attribute>`;
  });
  const links = relations.map(({ name, action, targets }) => {
    assertName(name);
    if (!['set', 'add', 'remove'].includes(action)) throw new Error('Invalid relationship action.');
    if (!Array.isArray(targets) || (action === 'set' && targets.length > 1) || (action !== 'set' && !targets.length)) throw new Error('set accepts zero or one target; add/remove require targets.');
    targets.forEach(oid => oidPath(oid, false));
    const members = targets.map(oid => `<Asset idref="${oid}"${action === 'set' ? '' : ` act="${action}"`}/>`).join('');
    return `<Relation name="${name}"${action === 'set' ? ' act="set"' : ''}>${members}</Relation>`;
  });
  if (!scalar.length && !links.length) throw new Error('Provide at least one attribute or relationship.');
  return `<Asset>${scalar.join('')}${links.join('')}</Asset>`;
}
const parser = new XMLParser({ ignoreAttributes: false, attributeNamePrefix: '@', parseTagValue: false, parseAttributeValue: false, trimValues: false,
  isArray: name => ['Asset', 'Attribute', 'Relation', 'Value'].includes(name),
});
export function parseXml(text) {
  if (/<!DOCTYPE|<!ENTITY/i.test(text)) throw new Error('DTD and entity declarations are not accepted.');
  if (XMLValidator.validate(text) !== true) throw new Error('Agility returned invalid XML.');
  return parser.parse(text);
}
function asset(node) {
  const attributes = Object.fromEntries((node.Attribute ?? []).map(item => [item['@name'], item.Value ? item.Value.map(value => typeof value === 'object' ? value['#text'] ?? '' : value) : item['#text'] ?? '']));
  const relations = Object.fromEntries((node.Relation ?? []).map(item => [item['@name'], (item.Asset ?? []).map(ref => ({ oid: ref['@idref'] ?? ref['@id'], href: ref['@href'],
    ...(ref.Attribute?.length ? { attributes: asset(ref).attributes } : {}) }))]));
  return { oid: node['@id'], href: node['@href'], attributes, relations };
}
export function parseAssets(text) {
  const doc = parseXml(text);
  if (Object.hasOwn(doc, 'Assets')) {
    if (doc.Assets == null || typeof doc.Assets !== 'object') return { assets: [] };
    const total = Number(doc.Assets['@total']);
    return { assets: (doc.Assets.Asset ?? []).map(asset), ...(Number.isFinite(total) ? { total } : {}) };
  }
  if (doc.Asset) return { assets: doc.Asset.map(asset) };
  throw new Error('Agility returned an unexpected XML document (expected Asset or Assets).');
}
