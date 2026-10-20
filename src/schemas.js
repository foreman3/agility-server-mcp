import * as z from 'zod/v4';
import { namePattern, oidPattern } from './xml.js';

export const name = z.string().regex(namePattern).max(128);
export const oid = z.string().regex(oidPattern).max(160).describe('Internal OID, e.g. Story:1234, not the display number S-01234.');
export const currentOid = z.string().regex(/^[A-Za-z][A-Za-z0-9_]*:\d+$/).max(160);
export const token = z.string().min(1).max(512).regex(/^[^\x00-\x1f,]+$/).describe('Agility selection token, e.g. Scope.Name or Children.@Count.');
export const selection = z.array(token).min(1).max(100);
export const page = { pageSize: z.int().min(1).max(1000).default(100), start: z.int().min(0).max(Number.MAX_SAFE_INTEGER).default(0) };
const scalar = z.union([z.string().max(100000), z.number().finite(), z.boolean(), z.null()]);
export const relation = z.strictObject({ name, action: z.enum(['set', 'add', 'remove']), targets: z.array(currentOid).max(100) }).superRefine((value, ctx) => {
  if (value.action === 'set' ? value.targets.length > 1 : value.targets.length === 0) ctx.addIssue({ code: 'custom', message: 'set accepts zero or one target; add/remove require at least one target.' });
});
export const mutation = {
  attributes: z.record(name, scalar).refine(value => Object.keys(value).length <= 100, 'At most 100 attributes.').default({}),
  relations: z.array(relation).max(100).default([]),
};
const nestedSelect = depth => z.array(depth > 0 ? z.union([token, z.strictObject({ from: name, select: nestedSelect(depth - 1) })]) : token).min(1).max(100);
export const query = z.strictObject({
  from: name,
  select: nestedSelect(3).default(['Name']),
  where: z.record(token, scalar).optional(),
  filter: z.array(z.string().min(1).max(4096)).max(50).optional(),
  with: z.record(z.string().regex(/^\$[A-Za-z][A-Za-z0-9_]*$/), scalar).optional(),
  sort: z.array(token).max(20).optional(),
  find: z.string().max(1000).optional(),
  findin: selection.optional(),
  asof: z.string().min(1).max(64).optional(),
  page: z.strictObject({ size: z.int().min(1).max(1000).default(100), start: z.int().min(0).max(Number.MAX_SAFE_INTEGER).default(0) }).default({ size: 100, start: 0 }),
});
