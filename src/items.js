import { assertName, oidPattern } from './xml.js';

const itemTypes = new Set(['Epic', 'Story', 'Defect', 'TestSet', 'PrimaryWorkitem', 'Workitem']);
export const isItemType = type => itemTypes.has(type);
export const canonicalOid = oid => typeof oid === 'string' && oidPattern.test(oid) ? oid.split(':').slice(0, 2).join(':') : null;
export const parentSelection = ['Super', 'Super.Name', 'Super.Number'];
export function itemSelection(type, select) {
  const nestedSuper = select.some(field => field && typeof field === 'object' && field.from === 'Super');
  return [...new Set([...select, ...(isItemType(type) ? parentSelection.filter(field => field !== 'Super' || !nestedSuper) : [])])];
}
const first = value => Array.isArray(value) ? value[0] : value;
function refOid(value) {
  const ref = first(value);
  return canonicalOid(typeof ref === 'string' ? ref : ref?.oid ?? ref?._oid ?? ref?.idref ?? ref?.id);
}
export function withParent(item, type = (item.oid ?? item._oid ?? '').split(':')[0]) {
  if (!isItemType(type)) return item;
  const attributes = item.attributes ?? item;
  const relation = item.relations?.Super;
  const reference = first(relation ?? attributes.Super ?? attributes['Super.ID']);
  const id = refOid(reference);
  const name = first(attributes['Super.Name']) ?? reference?.attributes?.Name ?? reference?.Name ?? null;
  const number = first(attributes['Super.Number']) ?? reference?.attributes?.Number ?? reference?.Number ?? null;
  const selected = relation !== undefined || ['Super', 'Super.ID', 'Super.Name', 'Super.Number'].some(key => Object.hasOwn(attributes, key));
  const parent = id ? { oid: id, id, name: name || null, number: number || null, relation: 'Super' } : null;
  return { ...item, parent, parentId: id, parentName: parent?.name ?? null, parentNumber: parent?.number ?? null,
    parentStatus: id ? (parent.name === null ? 'unresolved' : 'resolved')
      : selected && reference == null && !name ? 'none' : 'unavailable' };
}

export function prepareQueries(queries) {
  return queries.map(query => {
    const select = (query.select ?? ['Name']).map(value => typeof value === 'string' ? value : prepareQueries([value])[0]);
    // These relation names unambiguously yield workitems in the Agility model.
    const type = ['Subs', 'SubsAndDown', 'Dependencies', 'Dependants', 'Workitems'].includes(query.from) ? 'Workitem' : query.from;
    return { ...query, select: itemSelection(type, select) };
  });
}
export function normalizeQueryItems(value) {
  if (Array.isArray(value)) return value.map(normalizeQueryItems);
  if (!value || typeof value !== 'object') return value;
  const result = Object.fromEntries(Object.entries(value).map(([key, child]) => [key, normalizeQueryItems(child)]));
  return value._oid ? withParent(result) : result;
}

const array = value => value === undefined ? [] : Array.isArray(value) ? value : [value];
const flag = value => String(value).toLowerCase() === 'true';
export function describeMetadata(document, expectedType) {
  const node = array(document.AssetType ?? document.Meta?.AssetType).find(value => value['@name'] === expectedType);
  if (!node) throw new Error(`Agility metadata did not describe ${expectedType}.`);
  const fields = Object.fromEntries(array(node.AttributeDefinition).map(value => {
    const name = assertName(value['@name']);
    const reciprocal = value.ReciprocalRelation?.['@tokenref'];
    const target = value.RelatedAsset?.['@nameref'];
    return [name, { name, type: value['@attributetype'], required: flag(value['@isrequired']), readOnly: flag(value['@isreadonly']),
      multiValue: flag(value['@ismultivalue']), custom: flag(value['@iscustom']),
      ...(target ? { targetType: assertName(target) } : {}),
      ...(reciprocal && /^[A-Za-z][A-Za-z0-9_]*\.[A-Za-z][A-Za-z0-9_]*$/.test(reciprocal) ? { reciprocal: reciprocal.split('.')[1] } : {}),
    }];
  }));
  return { assetType: expectedType, baseType: node.Base?.['@nameref'] ?? null, fields,
    operations: array(node.Operation).map(value => value['@name']).filter(Boolean), version: node['@version'] ?? null };
}

export function itemSummary(item) {
  const attributes = item.attributes ?? item;
  const oid = canonicalOid(item.oid ?? item._oid);
  return { oid, assetType: oid?.split(':')[0] ?? null, name: attributes.Name ?? null, number: attributes.Number ?? null,
    ...(item.parentStatus ? { parent: item.parent, parentId: item.parentId, parentName: item.parentName, parentStatus: item.parentStatus } : {}) };
}
