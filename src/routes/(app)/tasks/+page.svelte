<script lang="ts">
	import { getContext, onMount, tick } from 'svelte';
	import { showArchivedChats, showSidebar, mobile, user } from '$lib/stores';
	import { WEBUI_API_BASE_URL } from '$lib/constants';
	import { toast } from 'svelte-sonner';

	import {
		deleteGenerationTask,
		downloadGenerationTask,
		getGenerationTaskPreview,
		listGenerationTaskUsers,
		listGenerationTasks,
		type GenerationTaskItem,
		type GenerationTaskUserItem
	} from '$lib/apis/generation-tasks';

	import UserMenu from '$lib/components/layout/Sidebar/UserMenu.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Sidebar from '$lib/components/icons/Sidebar.svelte';
	import Modal from '$lib/components/common/Modal.svelte';
	import Download from '$lib/components/icons/Download.svelte';
	import GarbageBin from '$lib/components/icons/GarbageBin.svelte';
	import Refresh from '$lib/components/icons/Refresh.svelte';

	const i18n = getContext('i18n');

	let loading = false;
	let loadingMore = false;
	let hasMore = true;
	let offset = 0;
	const PAGE_SIZE = 48;

	let tasks: GenerationTaskItem[] = [];
	let taskUsers: GenerationTaskUserItem[] = [];

	let selectedUserId = '';
	let selectedStatus = '';
	let selectedModel = '';
	let includeDeleted = false;

	let showPreview = false;
	let selectedTask: GenerationTaskItem | null = null;

	const STATUS_OPTIONS = ['', 'PENDING', 'RUNNING', 'SUCCEEDED', 'FAILED', 'CANCELED', 'UNKNOWN'];

	const toAssetUrl = (value?: string | null) => {
		if (!value) return '';
		return value.startsWith('/') ? `${WEBUI_API_BASE_URL}${value}` : value;
	};

	const statusLabel = (value?: string | null) => {
		const normalized = String(value || '').toUpperCase();
		return normalized || 'UNKNOWN';
	};

	const resetAndLoadTasks = async () => {
		offset = 0;
		hasMore = true;
		tasks = [];
		await loadTasks({ reset: true });
	};

	const loadTaskUsers = async () => {
		taskUsers = await listGenerationTaskUsers(localStorage.token).catch((error) => {
			console.error(error);
			return [];
		});
	};

	const loadTasks = async ({ reset = false }: { reset?: boolean } = {}) => {
		if (reset) {
			loading = true;
		} else {
			if (loadingMore || !hasMore) return;
			loadingMore = true;
		}

		try {
			const rows = await listGenerationTasks(localStorage.token, {
				user_id: selectedUserId || undefined,
				status: selectedStatus || undefined,
				model: selectedModel.trim() || undefined,
				include_deleted: includeDeleted,
				refresh_status: true,
				offset,
				limit: PAGE_SIZE
			});
			if (reset) {
				tasks = rows;
			} else {
				tasks = [...tasks, ...rows];
			}
			offset += rows.length;
			hasMore = rows.length === PAGE_SIZE;
		} catch (error) {
			toast.error(`${error}`);
		} finally {
			loading = false;
			loadingMore = false;
		}
	};

	const refreshPreviewTask = async (task: GenerationTaskItem) => {
		const preview = await getGenerationTaskPreview(localStorage.token, task.task_id).catch(() => null);
		if (!preview) return task;

		return {
			...task,
			status: preview.status ?? task.status,
			archive_status: preview.archive_status ?? task.archive_status,
			download_ready: preview.download_ready ?? task.download_ready,
			can_delete: preview.can_delete ?? task.can_delete,
			thumbnail_url: preview.thumbnail_url ?? task.thumbnail_url,
			video_preview_url: preview.video_preview_url ?? task.video_preview_url
		};
	};

	const openPreview = async (task: GenerationTaskItem) => {
		selectedTask = task;
		showPreview = true;

		const fresh = await refreshPreviewTask(task);
		selectedTask = fresh;
		tasks = tasks.map((item) => (item.task_id === fresh.task_id ? fresh : item));
	};

	const handleDownload = async (task: GenerationTaskItem) => {
		if (!task.download_ready) {
			toast.error('任务归档尚未完成');
			return;
		}

		try {
			const blob = await downloadGenerationTask(localStorage.token, task.task_id);
			const objectUrl = URL.createObjectURL(blob);
			const a = document.createElement('a');
			a.href = objectUrl;
			a.download = `${task.task_id}.mp4`;
			document.body.appendChild(a);
			a.click();
			document.body.removeChild(a);
			URL.revokeObjectURL(objectUrl);
		} catch (error) {
			toast.error(`${error}`);
		}
	};

	const handleDelete = async (task: GenerationTaskItem) => {
		if (!task.can_delete) {
			toast.error('无删除权限');
			return;
		}

		const confirmed = window.confirm('确定删除该任务吗？该操作为软删除。');
		if (!confirmed) return;

		try {
			await deleteGenerationTask(localStorage.token, task.task_id);
			toast.success('任务已删除');
			showPreview = false;
			selectedTask = null;
			await resetAndLoadTasks();
		} catch (error) {
			toast.error(`${error}`);
		}
	};

	onMount(async () => {
		await loadTaskUsers();
		await resetAndLoadTasks();
	});
</script>

<svelte:head>
	<title>{$i18n.t('Tasks')}</title>
</svelte:head>

<div
	class="flex flex-col w-full h-screen max-h-[100dvh] transition-width duration-200 ease-in-out {$showSidebar
		? 'md:max-w-[calc(100%-var(--sidebar-width))]'
		: ''} max-w-full"
>
	<nav class="px-2 pt-1.5 backdrop-blur-xl w-full drag-region">
		<div class="flex items-center">
			{#if $mobile}
				<div class="{$showSidebar ? 'md:hidden' : ''} flex flex-none items-center">
					<Tooltip
						content={$showSidebar ? $i18n.t('Close Sidebar') : $i18n.t('Open Sidebar')}
						interactive={true}
					>
						<button
							id="sidebar-toggle-button"
							class="cursor-pointer flex rounded-lg hover:bg-gray-100 dark:hover:bg-gray-850 transition"
							on:click={() => {
								showSidebar.set(!$showSidebar);
							}}
						>
							<div class="self-center p-1.5">
								<Sidebar />
							</div>
						</button>
					</Tooltip>
				</div>
			{/if}

			<div class="ml-2 py-0.5 self-center flex items-center justify-between w-full">
				<div class="flex gap-1 scrollbar-none overflow-x-auto w-fit text-center text-sm font-medium bg-transparent py-1 touch-auto pointer-events-auto">
					<span class="min-w-fit transition">{$i18n.t('Tasks')}</span>
				</div>

				<div class="self-center flex items-center gap-1">
					{#if $user !== undefined && $user !== null}
						<UserMenu
							className="w-[240px]"
							role={$user?.role}
							help={true}
							on:show={(e) => {
								if (e.detail === 'archived-chat') {
									showArchivedChats.set(true);
								}
							}}
						>
							<button
								class="select-none flex rounded-xl p-1.5 w-full hover:bg-gray-50 dark:hover:bg-gray-850 transition"
								aria-label="User Menu"
							>
								<div class="self-center">
									<img
										src={`${WEBUI_API_BASE_URL}/users/${$user?.id}/profile/image`}
										class="size-6 object-cover rounded-full"
										alt="User profile"
										draggable="false"
									/>
								</div>
							</button>
						</UserMenu>
					{/if}
				</div>
			</div>
		</div>
	</nav>

	<div class="flex-1 overflow-y-auto px-3 pb-4">
		<div class="sticky top-0 z-10 bg-gray-50/85 dark:bg-gray-950/85 backdrop-blur-sm pt-2 pb-3">
			<div class="task-filter-grid">
				<select class="task-select" bind:value={selectedUserId}>
					<option value="">全部用户</option>
					{#each taskUsers as item (item.user_id)}
						<option value={item.user_id}>{item.user_name}</option>
					{/each}
				</select>

				<select class="task-select" bind:value={selectedStatus}>
					{#each STATUS_OPTIONS as status}
						<option value={status}>{status || '全部状态'}</option>
					{/each}
				</select>

				<input
					class="task-input"
					placeholder="按模型过滤（可选）"
					bind:value={selectedModel}
					on:keydown={(e) => {
						if (e.key === 'Enter') {
							resetAndLoadTasks();
						}
					}}
				/>

				<label class="task-checkbox-wrap">
					<input type="checkbox" bind:checked={includeDeleted} />
					<span>包含已删除</span>
				</label>

				<button
					class="task-btn"
					on:click={() => {
						resetAndLoadTasks();
					}}
				>
					查询
				</button>

				<button
					class="task-btn task-btn-icon"
					on:click={() => {
						resetAndLoadTasks();
					}}
					aria-label="refresh"
				>
					<Refresh className="size-4" />
				</button>
			</div>
		</div>

		{#if loading && tasks.length === 0}
			<div class="h-full flex items-center justify-center text-sm text-gray-500">加载中...</div>
		{:else if tasks.length === 0}
			<div class="h-full flex items-center justify-center text-sm text-gray-500">暂无任务</div>
		{:else}
			<div class="task-grid">
				{#each tasks as task (task.task_id)}
					<div class="task-cell">
						<div
							class="task-preview"
							role="button"
							tabindex="0"
							on:click={() => {
								openPreview(task);
							}}
							on:keydown={(event) => {
								if (event.key === 'Enter' || event.key === ' ') {
									event.preventDefault();
									openPreview(task);
								}
							}}
						>
							{#if task.thumbnail_url}
								<img
									src={toAssetUrl(task.thumbnail_url)}
									alt={`task-${task.task_id}`}
									class="w-full h-full object-cover"
									loading="lazy"
								/>
							{:else if task.video_preview_url}
								<!-- svelte-ignore a11y_media_has_caption -->
								<video
									class="w-full h-full object-cover"
									src={toAssetUrl(task.video_preview_url)}
									muted
									playsinline
									preload="metadata"
								></video>
							{:else}
								<div class="w-full h-full flex items-center justify-center text-xs text-gray-500 px-2 text-center">
									{statusLabel(task.status)}
								</div>
							{/if}

							<div class="task-overlay-top">
								<button
									type="button"
									class="task-icon-btn"
									disabled={!task.download_ready}
									on:click|stopPropagation={() => {
										handleDownload(task);
									}}
									aria-label="download"
								>
									<Download className="size-4" />
								</button>
							</div>
						</div>

						<div class="task-meta">
							<div class="task-meta-line">{task.user_name || task.user_id || '-'}</div>
							<div class="task-meta-line task-meta-dim">{task.task_id}</div>
							<div class="task-meta-line task-meta-dim">
								{statusLabel(task.status)} / {statusLabel(task.archive_status)}
							</div>
						</div>
					</div>
				{/each}
			</div>

			{#if hasMore}
				<div class="w-full flex justify-center py-4">
					<button
						class="task-btn"
						disabled={loadingMore}
						on:click={() => {
							loadTasks();
						}}
					>
						{loadingMore ? '加载中...' : '加载更多'}
					</button>
				</div>
			{/if}
		{/if}
	</div>
</div>

<Modal bind:show={showPreview} size="xl">
	{#if selectedTask}
		<div class="p-4">
			<div class="flex items-center justify-between gap-3 mb-3">
				<div class="text-sm font-medium truncate">{selectedTask.task_id}</div>
				<div class="flex items-center gap-2">
					<button
						type="button"
						class="task-icon-btn modal-icon"
						disabled={!selectedTask.download_ready}
						on:click={() => {
							handleDownload(selectedTask);
						}}
						aria-label="download"
					>
						<Download className="size-4" />
					</button>

					{#if selectedTask.can_delete}
						<button
							type="button"
							class="task-icon-btn modal-icon danger"
							on:click={() => {
								handleDelete(selectedTask);
							}}
							aria-label="delete"
						>
							<GarbageBin className="size-4" />
						</button>
					{/if}
				</div>
			</div>

			<div class="rounded-xl overflow-hidden bg-black">
				{#if selectedTask.video_preview_url}
					<!-- svelte-ignore a11y_media_has_caption -->
					<video
						class="w-full max-h-[72vh]"
						src={toAssetUrl(selectedTask.video_preview_url)}
						controls
						playsinline
						preload="metadata"
					></video>
				{:else}
					<div class="w-full h-[40vh] flex items-center justify-center text-sm text-gray-400">
						暂无可预览视频
					</div>
				{/if}
			</div>
		</div>
	{/if}
</Modal>

<style>
	.task-filter-grid {
		display: grid;
		grid-template-columns: 1fr 1fr 1.2fr auto auto auto;
		gap: 0.5rem;
	}

	.task-select,
	.task-input {
		border-radius: 0.75rem;
		border: 1px solid rgba(156, 163, 175, 0.35);
		background: rgba(255, 255, 255, 0.7);
		padding: 0.55rem 0.75rem;
		font-size: 0.875rem;
	}

	:global(.dark) .task-select,
	:global(.dark) .task-input {
		background: rgba(17, 24, 39, 0.7);
		border-color: rgba(75, 85, 99, 0.45);
	}

	.task-checkbox-wrap {
		display: inline-flex;
		align-items: center;
		gap: 0.35rem;
		font-size: 0.8rem;
		padding: 0 0.25rem;
	}

	.task-btn {
		border-radius: 0.75rem;
		padding: 0.55rem 0.9rem;
		font-size: 0.85rem;
		border: 1px solid rgba(156, 163, 175, 0.35);
		background: rgba(255, 255, 255, 0.75);
	}

	:global(.dark) .task-btn {
		background: rgba(17, 24, 39, 0.8);
		border-color: rgba(75, 85, 99, 0.45);
	}

	.task-btn:disabled {
		opacity: 0.6;
		cursor: not-allowed;
	}

	.task-btn-icon {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		width: 2.15rem;
		padding: 0;
	}

	.task-grid {
		display: grid;
		gap: 0.75rem;
		grid-template-columns: repeat(3, minmax(0, 1fr));
	}

	@media (min-width: 960px) {
		.task-grid {
			grid-template-columns: repeat(4, minmax(0, 1fr));
		}
	}

	@media (min-width: 1200px) {
		.task-grid {
			grid-template-columns: repeat(5, minmax(0, 1fr));
		}
	}

	@media (min-width: 1480px) {
		.task-grid {
			grid-template-columns: repeat(6, minmax(0, 1fr));
		}
	}

	@media (min-width: 1760px) {
		.task-grid {
			grid-template-columns: repeat(7, minmax(0, 1fr));
		}
	}

	@media (min-width: 2080px) {
		.task-grid {
			grid-template-columns: repeat(8, minmax(0, 1fr));
		}
	}

	.task-cell {
		min-width: 0;
	}

	.task-preview {
		display: block;
		width: 100%;
		aspect-ratio: 3 / 4;
		border-radius: 0.9rem;
		overflow: hidden;
		position: relative;
		background: rgba(229, 231, 235, 0.8);
	}

	:global(.dark) .task-preview {
		background: rgba(31, 41, 55, 0.8);
	}

	.task-overlay-top {
		position: absolute;
		top: 0.4rem;
		right: 0.4rem;
		display: flex;
		gap: 0.35rem;
	}

	.task-icon-btn {
		width: 1.9rem;
		height: 1.9rem;
		border-radius: 999px;
		display: inline-flex;
		align-items: center;
		justify-content: center;
		border: 1px solid rgba(255, 255, 255, 0.35);
		background: rgba(17, 24, 39, 0.6);
		color: #fff;
	}

	.task-icon-btn:disabled {
		opacity: 0.4;
		cursor: not-allowed;
	}

	.task-icon-btn.modal-icon {
		border-color: rgba(156, 163, 175, 0.45);
		background: rgba(243, 244, 246, 0.9);
		color: rgb(17, 24, 39);
	}

	:global(.dark) .task-icon-btn.modal-icon {
		background: rgba(31, 41, 55, 0.9);
		color: #fff;
	}

	.task-icon-btn.modal-icon.danger {
		color: #ef4444;
	}

	.task-meta {
		padding: 0.4rem 0.2rem 0;
	}

	.task-meta-line {
		font-size: 0.74rem;
		line-height: 1.25;
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}

	.task-meta-line.task-meta-dim {
		color: rgb(107, 114, 128);
	}

	:global(.dark) .task-meta-line.task-meta-dim {
		color: rgb(156, 163, 175);
	}

	@media (max-width: 960px) {
		.task-filter-grid {
			grid-template-columns: 1fr 1fr;
		}
	}
</style>
