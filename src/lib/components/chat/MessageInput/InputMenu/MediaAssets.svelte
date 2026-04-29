<script lang="ts">
	import { onMount, tick, getContext } from 'svelte';

	import { listMediaAssets, type MediaAssetItem } from '$lib/apis/media-assets';

	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Photo from '$lib/components/icons/Photo.svelte';
	import Voice from '$lib/components/icons/Voice.svelte';
	import Component from '$lib/components/icons/Component.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import Loader from '$lib/components/common/Loader.svelte';
	import ChevronRight from '$lib/components/icons/ChevronRight.svelte';
	import ChevronLeft from '$lib/components/icons/ChevronLeft.svelte';
	import Folder from '$lib/components/icons/Folder.svelte';

	const i18n = getContext('i18n');

	export let onSelect: (item: MediaAssetItem) => void = () => {};

	type TreeNode = {
		name: string;
		path: string;
		folders: Map<string, TreeNode>;
		assets: MediaAssetItem[];
	};

	let loaded = false;
	let items: MediaAssetItem[] = [];
	let selectedIdx = -1;

	let page = 0;
	const limit = 50;
	let itemsLoading = false;
	let allItemsLoaded = false;

	let currentPath = '';

	const toAssetPath = (item: MediaAssetItem) => {
		const raw = item?.relative_path || item?.display_name || item?.original_filename || '';
		return String(raw).replace(/\\/g, '/').replace(/^\/+/, '');
	};

	const toAssetLabel = (item: MediaAssetItem) => {
		const path = toAssetPath(item);
		const parts = path.split('/');
		return parts.at(-1) || path || item?.display_name || item?.original_filename || '';
	};

	const mediaLabel = (item: MediaAssetItem) => {
		if (item.media_type === 'image') return 'Image';
		if (item.media_type === 'video') return 'Video';
		if (item.media_type === 'audio') return 'Audio';
		return item.media_type;
	};

	const createNode = (name: string, path: string): TreeNode => ({
		name,
		path,
		folders: new Map<string, TreeNode>(),
		assets: []
	});

	const buildTree = (assets: MediaAssetItem[]): TreeNode => {
		const root = createNode('', '');
		for (const item of assets) {
			const path = toAssetPath(item);
			if (!path) {
				root.assets.push(item);
				continue;
			}

			const parts = path.split('/').filter((part) => !!part);
			if (parts.length === 0) {
				root.assets.push(item);
				continue;
			}

			let node = root;
			for (let i = 0; i < parts.length - 1; i++) {
				const seg = parts[i];
				const nextPath = node.path ? `${node.path}/${seg}` : seg;
				if (!node.folders.has(seg)) {
					node.folders.set(seg, createNode(seg, nextPath));
				}
				node = node.folders.get(seg);
			}
			node.assets.push(item);
		}
		return root;
	};

	const getNodeByPath = (root: TreeNode, path: string): TreeNode => {
		if (!path) return root;
		const parts = path.split('/').filter((part) => !!part);
		let node: TreeNode = root;
		for (const seg of parts) {
			const next = node.folders.get(seg);
			if (!next) return root;
			node = next;
		}
		return node;
	};

	const folderCount = (node: TreeNode): number => {
		let count = node.assets.length;
		for (const child of node.folders.values()) {
			count += folderCount(child);
		}
		return count;
	};

	const goUp = () => {
		if (!currentPath) return;
		const parts = currentPath.split('/').filter((part) => !!part);
		parts.pop();
		currentPath = parts.join('/');
	};

	const loadMoreItems = async () => {
		if (allItemsLoaded) return;
		page += 1;
		await getItemsPage();
	};

	const getItemsPage = async () => {
		itemsLoading = true;
		const offset = page * limit;
		const res = await listMediaAssets(localStorage.token, {
			limit,
			offset,
			status: 'active'
		}).catch(() => []);

		if ((res ?? []).length < limit) {
			allItemsLoaded = true;
		}

		items = [...items, ...(res ?? [])];
		itemsLoading = false;
		return res;
	};

	$: treeRoot = buildTree(items);
	$: currentNode = getNodeByPath(treeRoot, currentPath);
	$: folderItems = Array.from(currentNode.folders.values()).sort((a, b) =>
		a.name.localeCompare(b.name, undefined, { sensitivity: 'base' })
	);
	$: assetItems = [...currentNode.assets].sort((a, b) =>
		toAssetPath(a).localeCompare(toAssetPath(b), undefined, { sensitivity: 'base' })
	);
	$: breadcrumb = currentPath || '/';

	onMount(async () => {
		await getItemsPage();
		await tick();
		loaded = true;
	});
</script>

{#if loaded}
	{#if items.length === 0}
		<div class="text-center text-xs text-gray-500 py-3">{$i18n.t('No media assets found')}</div>
	{:else}
		<div class="flex flex-col gap-0.5">
			{#if currentPath}
				<button
					class="px-2.5 py-1 rounded-xl w-full text-left flex justify-between items-center text-sm hover:bg-gray-50 dark:hover:bg-gray-800/50"
					type="button"
					on:click|stopPropagation={goUp}
				>
					<div class="flex items-center gap-1.5 overflow-hidden">
						<ChevronLeft className="size-4 shrink-0 text-gray-500" />
						<div class="line-clamp-1">{$i18n.t('Back')}</div>
					</div>
					<div class="text-[11px] text-gray-500 shrink-0">{breadcrumb}</div>
				</button>
			{/if}

			{#each folderItems as folder, idx (`folder:${folder.path}`)}
				<button
					class="px-2.5 py-1 rounded-xl w-full text-left flex justify-between items-center text-sm hover:bg-gray-50 dark:hover:bg-gray-800/50"
					type="button"
					on:click|stopPropagation={() => {
						currentPath = folder.path;
						selectedIdx = -1;
					}}
				>
					<div class="flex items-center gap-1.5 overflow-hidden">
						<Folder className="size-4 shrink-0" />
						<div class="line-clamp-1">{folder.name}</div>
					</div>
					<div class="text-[11px] text-gray-500 shrink-0 flex items-center gap-1">
						{folderCount(folder)}
						<ChevronRight className="size-3.5" />
					</div>
				</button>
			{/each}

			{#each assetItems as item, idx (`asset:${item.asset_id}`)}
				<button
					class="px-2.5 py-1 rounded-xl w-full text-left flex justify-between items-center text-sm {idx ===
					selectedIdx
						? 'bg-gray-50 dark:bg-gray-800 dark:text-gray-100 selected-command-option-button'
						: 'hover:bg-gray-50 dark:hover:bg-gray-800/50'}"
					type="button"
					on:click|stopPropagation={() => {
						onSelect(item);
					}}
					on:mousemove={() => {
						selectedIdx = idx;
					}}
					data-selected={idx === selectedIdx}
				>
					<div class="text-black dark:text-gray-100 flex items-center gap-1.5 overflow-hidden">
						<Tooltip content={mediaLabel(item)} placement="top">
							{#if item.media_type === 'image'}
								<Photo className="size-4 shrink-0" />
							{:else if item.media_type === 'audio'}
								<Voice className="size-4 shrink-0" />
							{:else}
								<Component className="size-4 shrink-0" />
							{/if}
						</Tooltip>

						<Tooltip content={toAssetPath(item)} placement="top-start">
							<div class="line-clamp-1 flex-1">{toAssetLabel(item)}</div>
						</Tooltip>
					</div>

					<div class="text-[11px] text-gray-500 shrink-0">{mediaLabel(item)}</div>
				</button>
			{/each}

			{#if folderItems.length === 0 && assetItems.length === 0}
				<div class="text-center text-xs text-gray-500 py-3">{$i18n.t('Folder is empty')}</div>
			{/if}

			{#if !allItemsLoaded}
				<Loader
					on:visible={() => {
						if (!itemsLoading) {
							loadMoreItems();
						}
					}}
				>
					<div class="w-full flex justify-center py-4 text-xs animate-pulse items-center gap-2">
						<Spinner className=" size-4" />
						<div>{$i18n.t('Loading...')}</div>
					</div>
				</Loader>
			{/if}
		</div>
	{/if}
{:else}
	<div class="py-4.5">
		<Spinner />
	</div>
{/if}
