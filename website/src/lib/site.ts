/* Site settings, read once per build from src/content/settings/site.json. */
import { getCollection, type CollectionEntry } from 'astro:content';

export type Site = CollectionEntry<'settings'>['data'];

let cached: Site | undefined;

export async function getSite(): Promise<Site> {
  if (cached) return cached;
  const entries = await getCollection('settings');
  const entry = entries[0];
  if (!entry) throw new Error('No site settings found in src/content/settings/');
  cached = entry.data;
  return cached;
}
