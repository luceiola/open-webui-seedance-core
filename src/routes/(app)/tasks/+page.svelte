<script lang="ts">
	import { getContext, onMount } from 'svelte';
	import { showArchivedChats, showSidebar, mobile, user } from '$lib/stores';
	import { WEBUI_API_BASE_URL } from '$lib/constants';
	import { toast } from 'svelte-sonner';

	import {
		deleteGenerationTask,
		downloadGenerationTask,
		getGenerationTaskPreview,
		listGenerationTaskGroups,
			listGenerationTaskProviders,
			listGenerationTaskUsers,
			listGenerationTasks,
			type GenerationTaskGroupItem,
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
	import Clipboard from '$lib/components/icons/Clipboard.svelte';

	const i18n = getContext('i18n');

	let loading = false;
	let loadingMore = false;
	let hasMore = true;
	let offset = 0;
	const PAGE_SIZE = 48;

	let tasks: GenerationTaskItem[] = [];
	let taskUsers: GenerationTaskUserItem[] = [];
	let taskGroups: GenerationTaskGroupItem[] = [];
	let providerOptions: string[] = [];
	let totalTasks = 0;

	let selectedUserId = '';
	let selectedGroupId = '';
	let selectedProvider = '';
	let selectedStatus = '';
	let selectedTimePreset = '7d';
	let autoApplyTimer: ReturnType<typeof setTimeout> | null = null;
	let initialized = false;
	let lastAppliedFilterKey = '';

	let showPreview = false;
	let selectedTask: GenerationTaskItem | null = null;
	let selectedTaskPromptSegments: PromptSegment[] = [];
	let selectedTaskParamEntries: Array<[string, string]> = [];
	let selectedTaskIsFailed = false;
	let selectedTaskFailureReason = '';

	type PromptSegment = {
		text: string;
		url?: string;
	};

	const STATUS_OPTIONS = ['', 'PENDING', 'RUNNING', 'SUCCEEDED', 'FAILED', 'CANCELED', 'UNKNOWN'];
	const FALLBACK_PROVIDER_OPTIONS = ['', 'ark', 'happyhorse'];
	const TIME_PRESET_OPTIONS = [
		{ value: 'all', label: '全部时间' },
		{ value: '24h', label: '最近24小时' },
		{ value: '7d', label: '最近7天' },
		{ value: '30d', label: '最近30天' },
		{ value: '90d', label: '最近3个月' },
		{ value: '180d', label: '最近半年' }
	];

	const toAssetUrl = (value?: string | null) => {
		if (!value) return '';
		if (value.startsWith('/api/')) return value;
		return value.startsWith('/') ? `${WEBUI_API_BASE_URL}${value}` : value;
	};

	const statusLabel = (value?: string | null) => {
		const normalized = String(value || '').toUpperCase();
		return normalized || 'UNKNOWN';
	};

	const taskRowKey = (task: GenerationTaskItem) =>
		`${String(task.user_id || '')}::${String(task.task_id || '')}`;

	const resolveTimeRange = (): { startAt?: number; endAt?: number } => {
		const now = Math.floor(Date.now() / 1000);
		if (selectedTimePreset === '24h') {
			return { startAt: now - 24 * 3600, endAt: now };
		}
		if (selectedTimePreset === '7d') {
			return { startAt: now - 7 * 24 * 3600, endAt: now };
		}
		if (selectedTimePreset === '30d') {
			return { startAt: now - 30 * 24 * 3600, endAt: now };
		}
		if (selectedTimePreset === '90d') {
			return { startAt: now - 90 * 24 * 3600, endAt: now };
		}
		if (selectedTimePreset === '180d') {
			return { startAt: now - 180 * 24 * 3600, endAt: now };
		}
		return {};
	};

	const isTimeRangeValid = (_range: { startAt?: number; endAt?: number }): boolean => true;

	const getFilterStateKey = (): string =>
		JSON.stringify([
			selectedUserId || '',
			selectedGroupId || '',
			selectedProvider || '',
			selectedStatus || '',
			selectedTimePreset || ''
		]);

	const shouldRefreshStatus = () => {
		const normalized = String(selectedStatus || '').toUpperCase();
		if (!normalized) return true;
		return normalized === 'PENDING' || normalized === 'RUNNING';
	};

	const applyFiltersFromUrl = (defaultUserId: string) => {
		if (typeof window === 'undefined') {
			if (defaultUserId) selectedUserId = defaultUserId;
			return;
		}

		const query = new URLSearchParams(window.location.search);
		const urlUserId = String(query.get('user_id') || '').trim();
		selectedUserId = urlUserId || defaultUserId || '';
		selectedGroupId = String(query.get('group_id') || '').trim();
		selectedProvider = String(query.get('provider') || '').trim().toLowerCase();
		selectedStatus = String(query.get('status') || '').trim().toUpperCase();

		const urlPreset = String(query.get('time_preset') || '').trim();
		const supportedPreset = TIME_PRESET_OPTIONS.find((item) => item.value === urlPreset);
		if (supportedPreset) {
			selectedTimePreset = supportedPreset.value;
		} else {
			selectedTimePreset = '7d';
		}
	};

	const writeFiltersToUrl = () => {
		if (typeof window === 'undefined') return;

		const query = new URLSearchParams();

		if (selectedUserId) query.set('user_id', selectedUserId);
		if (selectedGroupId) query.set('group_id', selectedGroupId);
		if (selectedProvider) query.set('provider', selectedProvider);
		if (selectedStatus) query.set('status', selectedStatus);
		query.set('time_preset', selectedTimePreset);

		const queryString = query.toString();
		const nextUrl = `${window.location.pathname}${queryString ? `?${queryString}` : ''}`;
		window.history.replaceState(null, '', nextUrl);
	};

	const resetAndLoadTasks = async () => {
		offset = 0;
		hasMore = true;
		totalTasks = 0;
		tasks = [];
		await loadTasks({ reset: true });
	};

	const loadTaskUsers = async () => {
		taskUsers = await listGenerationTaskUsers(localStorage.token).catch((error) => {
			console.error(error);
			return [];
		});
	};

	const loadTaskGroups = async () => {
		taskGroups = await listGenerationTaskGroups(localStorage.token).catch((error) => {
			console.error(error);
			return [];
		});

		if (selectedGroupId && !taskGroups.some((item) => item.group_id === selectedGroupId)) {
			selectedGroupId = '';
		}
	};

	const loadTaskProviders = async () => {
		const rows = await listGenerationTaskProviders(localStorage.token, selectedUserId || undefined).catch(
			(error) => {
				console.error(error);
				return [];
			}
		);
		const uniq = Array.from(new Set(rows.map((item) => String(item).trim().toLowerCase()).filter(Boolean)));
		providerOptions = ['', ...uniq];
		if (providerOptions.length <= 1) {
			providerOptions = [...FALLBACK_PROVIDER_OPTIONS];
		}
		if (selectedProvider && !providerOptions.includes(selectedProvider)) {
			selectedProvider = '';
		}
	};

	const loadTasks = async ({ reset = false }: { reset?: boolean } = {}) => {
		if (reset) {
			loading = true;
		} else {
			if (loadingMore || !hasMore) return;
			loadingMore = true;
		}

		const range = resolveTimeRange();
		if (!isTimeRangeValid(range)) {
			toast.error('开始时间不能晚于结束时间');
			loading = false;
			loadingMore = false;
			return;
		}

		try {
			const result = await listGenerationTasks(localStorage.token, {
				user_id: selectedUserId || undefined,
				group_id: selectedGroupId || undefined,
					provider: selectedProvider || undefined,
					status: selectedStatus || undefined,
					start_at: range.startAt,
					end_at: range.endAt,
					include_deleted: false,
					refresh_status: shouldRefreshStatus(),
					offset,
					limit: PAGE_SIZE
				});

			const rows = result.items;
			totalTasks = result.total;
			if (reset) {
				tasks = rows;
			} else {
				tasks = [...tasks, ...rows];
			}
			offset += rows.length;
			hasMore = offset < totalTasks;
		} catch (error) {
			toast.error(`${error}`);
		} finally {
			loading = false;
			loadingMore = false;
		}
	};

	const applyFiltersNow = async ({ force = false }: { force?: boolean } = {}) => {
		const range = resolveTimeRange();
		if (!isTimeRangeValid(range)) {
			toast.error('开始时间不能晚于结束时间');
			return;
		}

		const filterKey = getFilterStateKey();
		if (!force && filterKey === lastAppliedFilterKey) {
			writeFiltersToUrl();
			return;
		}
		writeFiltersToUrl();
		await loadTaskProviders();
		await resetAndLoadTasks();
		lastAppliedFilterKey = getFilterStateKey();
	};

	const clearAutoApplyTimer = () => {
		if (!autoApplyTimer) return;
		clearTimeout(autoApplyTimer);
		autoApplyTimer = null;
	};

	const scheduleAutoApply = () => {
		if (!initialized) return;
		clearAutoApplyTimer();
		autoApplyTimer = setTimeout(() => {
			void applyFiltersNow();
		}, 200);
	};

	const refreshTasks = async () => {
		clearAutoApplyTimer();
		await applyFiltersNow({ force: true });
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
		tasks = tasks.map((item) =>
			item.task_id === fresh.task_id && String(item.user_id || '') === String(task.user_id || '')
				? fresh
				: item
		);
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

	const getPromptSegments = (task: GenerationTaskItem): PromptSegment[] => {
		const prompt = String(task.prompt_text || '');
		if (!prompt) return [];

		const resources = (task.prompt_resources || []).filter(
			(item) => item.url.startsWith('http://') || item.url.startsWith('https://')
		);
		const urlByRef = new Map<string, string>();
		resources.forEach((item) => {
			const rawName = String(item.name || '').trim();
			if (!rawName) return;
			urlByRef.set(rawName, item.url);
			urlByRef.set(rawName.replace(/^[@%]+/, ''), item.url);
		});
		const regex = /([@%])([^\s@%,，。；;:：!！?？)）\]】}》>"“”'`]+)/g;
		const segments: PromptSegment[] = [];
		let lastIdx = 0;
		let match: RegExpExecArray | null = null;
		while ((match = regex.exec(prompt)) !== null) {
			const full = match[0];
			const prefix = String(match[1] || '');
			const ref = String(match[2] || '').trim();
			if (match.index > lastIdx) {
				segments.push({ text: prompt.slice(lastIdx, match.index) });
			}
			const mappedUrl = urlByRef.get(ref) || urlByRef.get(`${prefix}${ref}`);
			if (mappedUrl) {
				segments.push({ text: full, url: mappedUrl });
			} else {
				segments.push({ text: full });
			}
			lastIdx = match.index + full.length;
		}
		if (lastIdx < prompt.length) {
			segments.push({ text: prompt.slice(lastIdx) });
		}
		if (segments.length === 0) {
			segments.push({ text: prompt });
		}
		return segments;
	};

	const getGenerationParamEntries = (task: GenerationTaskItem): Array<[string, string]> => {
		const rows: Array<[string, string]> = [];
		const pushIfPresent = (key: string, value: unknown) => {
			if (value === undefined || value === null) return;
			const text = String(value).trim();
			if (!text) return;
			rows.push([key, text]);
		};

		const params = task.generation_params;
		if (params && typeof params === 'object') {
			Object.entries(params).forEach(([key, value]) => {
				if (String(key).toLowerCase() === 'model') return;
				pushIfPresent(key, value);
			});
			return rows;
		}

		pushIfPresent('duration', task.duration);
		pushIfPresent('ratio', task.ratio);
		if (task.watermark !== undefined && task.watermark !== null) {
			pushIfPresent('watermark', task.watermark ? 'true' : 'false');
		}
		if (task.generate_audio !== undefined && task.generate_audio !== null) {
			pushIfPresent('generate_audio', task.generate_audio ? 'true' : 'false');
		}
		return rows;
	};

	const copyWithExecCommand = (text: string): boolean => {
		try {
			const textarea = document.createElement('textarea');
			textarea.value = text;
			textarea.setAttribute('readonly', 'readonly');
			textarea.style.position = 'fixed';
			textarea.style.top = '-9999px';
			textarea.style.left = '-9999px';
			textarea.style.opacity = '0';
			document.body.appendChild(textarea);
			textarea.focus();
			textarea.select();
			textarea.setSelectionRange(0, textarea.value.length);
			const copied = document.execCommand('copy');
			document.body.removeChild(textarea);
			return Boolean(copied);
		} catch (error) {
			console.warn('execCommand copy failed', error);
			return false;
		}
	};

	const copyText = async (text: string) => {
		// Try sync copy first to preserve user-gesture context in HTTP/intranet deployments.
		if (copyWithExecCommand(text)) {
			return;
		}

		if (navigator?.clipboard?.writeText) {
			try {
				await navigator.clipboard.writeText(text);
				return;
			} catch (error) {
				console.warn('clipboard.writeText failed', error);
			}
		}

		throw new Error('clipboard unavailable');
	};

	const isFailedTask = (task: GenerationTaskItem): boolean => {
		const status = String(task.status || '').toUpperCase();
		const archiveStatus = String(task.archive_status || '').toUpperCase();
		return status === 'FAILED' || archiveStatus === 'FAILED';
	};

	const getFailureReason = (task: GenerationTaskItem): string => {
		const errorMessage = String(task.error_message || '').trim();
		if (errorMessage) return errorMessage;
		const errorCode = String(task.error_code || '').trim();
		if (errorCode) return `错误码: ${errorCode}`;
		const archiveError = String(task.archive_error || '').trim();
		if (archiveError) return archiveError;
		return '暂无错误详情';
	};

	const copyPromptText = async (task: GenerationTaskItem) => {
		const prompt = String(task.prompt_text || '').trim();
		if (!prompt) {
			toast.error('提示词为空，无法复制');
			return;
		}
		try {
			await copyText(prompt);
			toast.success('已复制提示词');
		} catch (error) {
			toast.error(`复制失败: ${error}`);
		}
	};

	$: selectedTaskPromptSegments = selectedTask ? getPromptSegments(selectedTask) : [];
	$: selectedTaskParamEntries = selectedTask ? getGenerationParamEntries(selectedTask) : [];
	$: selectedTaskIsFailed = selectedTask ? isFailedTask(selectedTask) : false;
	$: selectedTaskFailureReason = selectedTask ? getFailureReason(selectedTask) : '';
	$: if (initialized) {
		selectedUserId;
		selectedGroupId;
		selectedProvider;
		selectedStatus;
		selectedTimePreset;
		scheduleAutoApply();
	}

	onMount(async () => {
		const defaultUserId = String($user?.id || '').trim();
		applyFiltersFromUrl(defaultUserId);
		await loadTaskUsers();
		await loadTaskGroups();
		await loadTaskProviders();
		writeFiltersToUrl();
		await resetAndLoadTasks();
		lastAppliedFilterKey = getFilterStateKey();
		initialized = true;

		return () => {
			clearAutoApplyTimer();
		};
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

				<select class="task-select" bind:value={selectedGroupId}>
					<option value="">全部用户组</option>
					{#each taskGroups as item (item.group_id)}
						<option value={item.group_id}>{item.group_name}</option>
					{/each}
				</select>

				<select class="task-select" bind:value={selectedProvider}>
					{#each providerOptions as provider}
						<option value={provider}>{provider ? provider.toUpperCase() : '全部来源'}</option>
					{/each}
				</select>

				<select class="task-select" bind:value={selectedStatus}>
					{#each STATUS_OPTIONS as status}
						<option value={status}>{status || '全部状态'}</option>
					{/each}
				</select>

				<select class="task-select" bind:value={selectedTimePreset}>
					{#each TIME_PRESET_OPTIONS as option}
						<option value={option.value}>{option.label}</option>
					{/each}
				</select>

				<button class="task-refresh-square" on:click={refreshTasks} aria-label="refresh">
					<Refresh className="size-4" />
				</button>
			</div>

			<div class="task-filter-actions">
				<div class="task-count-line">当前显示 {tasks.length} / {totalTasks}</div>
			</div>
		</div>

		{#if loading && tasks.length === 0}
			<div class="h-full flex items-center justify-center text-sm text-gray-500">加载中...</div>
		{:else if tasks.length === 0}
			<div class="h-full flex items-center justify-center text-sm text-gray-500">暂无任务</div>
		{:else}
			<div class="task-grid">
				{#each tasks as task (taskRowKey(task))}
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
				{:else if selectedTask.thumbnail_url}
					<img
						class="w-full max-h-[72vh] object-contain"
						src={toAssetUrl(selectedTask.thumbnail_url)}
						alt={`task-preview-${selectedTask.task_id}`}
						loading="lazy"
					/>
				{:else}
					<div class="w-full h-[40vh] flex items-center justify-center text-sm text-gray-400">
						暂无可预览视频
					</div>
				{/if}
			</div>

				<div class="task-detail-section">
					<div class="task-detail-title task-detail-title-row">
						<span>提示词</span>
						<button
							type="button"
							class="task-title-copy-btn"
							disabled={!selectedTask.prompt_text || !String(selectedTask.prompt_text).trim()}
							on:click={() => {
								copyPromptText(selectedTask);
							}}
							aria-label="copy-prompt"
						>
							<Clipboard className="size-3.5" />
							<span>复制提示词</span>
						</button>
					</div>
					<div class="task-detail-body">
						{#if selectedTaskPromptSegments.length === 0}
							<div class="task-detail-empty">暂无提示词</div>
					{:else}
						<p class="task-prompt-text">
							{#each selectedTaskPromptSegments as segment, idx (`prompt-${selectedTask.task_id}-${idx}`)}
								{#if segment.url}
									<a href={segment.url} target="_blank" rel="noreferrer noopener">{segment.text}</a>
								{:else}
									<span>{segment.text}</span>
								{/if}
							{/each}
						</p>
					{/if}
				</div>
			</div>

				<div class="task-detail-section">
					<div class="task-detail-title">生成参数</div>
					<div class="task-detail-body">
						{#if selectedTaskParamEntries.length === 0}
						<div class="task-detail-empty">暂无参数</div>
					{:else}
						<div class="task-param-grid">
							{#each selectedTaskParamEntries as row (`param-${selectedTask.task_id}-${row[0]}`)}
								<div class="task-param-key">{row[0]}</div>
								<div class="task-param-value">{row[1]}</div>
							{/each}
						</div>
						{/if}
					</div>
				</div>

				{#if selectedTaskIsFailed}
					<div class="task-detail-section">
						<div class="task-detail-title">失败原因</div>
						<div class="task-detail-body">
							<p class="task-prompt-text">{selectedTaskFailureReason}</p>
						</div>
					</div>
				{/if}
			</div>
		{/if}
</Modal>

<style>
	.task-filter-grid {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
		gap: 0.5rem;
	}

	.task-filter-actions {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		flex-wrap: wrap;
		margin-top: 0.55rem;
	}

	.task-select {
		border-radius: 0.75rem;
		border: 1px solid rgba(156, 163, 175, 0.35);
		background: rgba(255, 255, 255, 0.7);
		padding: 0.55rem 0.75rem;
		font-size: 0.875rem;
		height: 2.5rem;
	}

	:global(.dark) .task-select {
		background: rgba(17, 24, 39, 0.7);
		border-color: rgba(75, 85, 99, 0.45);
	}

	.task-refresh-square {
		border-radius: 0.75rem;
		width: 2.5rem;
		height: 2.5rem;
		padding: 0;
		display: inline-flex;
		align-items: center;
		justify-content: center;
		border: 1px solid rgba(156, 163, 175, 0.35);
		background: rgba(255, 255, 255, 0.75);
	}

	:global(.dark) .task-refresh-square {
		background: rgba(17, 24, 39, 0.8);
		border-color: rgba(75, 85, 99, 0.45);
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

	.task-count-line {
		margin-left: auto;
		font-size: 0.8rem;
		color: rgb(107, 114, 128);
	}

	:global(.dark) .task-count-line {
		color: rgb(156, 163, 175);
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

	.task-detail-section {
		margin-top: 0.8rem;
		border: 1px solid rgba(156, 163, 175, 0.3);
		border-radius: 0.75rem;
		overflow: hidden;
	}

	:global(.dark) .task-detail-section {
		border-color: rgba(75, 85, 99, 0.55);
	}

	.task-detail-title {
		font-size: 0.8rem;
		font-weight: 600;
		padding: 0.55rem 0.7rem;
		background: rgba(243, 244, 246, 0.7);
	}

	.task-detail-title-row {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 0.5rem;
	}

	.task-title-copy-btn {
		display: inline-flex;
		align-items: center;
		gap: 0.3rem;
		font-size: 0.74rem;
		font-weight: 500;
		line-height: 1;
		padding: 0.3rem 0.45rem;
		border-radius: 0.55rem;
		border: 1px solid rgba(156, 163, 175, 0.45);
		background: rgba(255, 255, 255, 0.75);
	}

	.task-title-copy-btn:disabled {
		opacity: 0.55;
		cursor: not-allowed;
	}

	:global(.dark) .task-detail-title {
		background: rgba(31, 41, 55, 0.65);
	}

	:global(.dark) .task-title-copy-btn {
		border-color: rgba(75, 85, 99, 0.55);
		background: rgba(17, 24, 39, 0.75);
	}

	.task-detail-body {
		padding: 0.7rem;
	}

	.task-detail-empty {
		font-size: 0.8rem;
		color: rgb(107, 114, 128);
	}

	.task-prompt-text {
		margin: 0;
		font-size: 0.86rem;
		line-height: 1.5;
		white-space: pre-wrap;
		word-break: break-word;
	}

	.task-prompt-text a {
		color: rgb(37, 99, 235);
		text-decoration: underline;
	}

	:global(.dark) .task-prompt-text a {
		color: rgb(96, 165, 250);
	}

	.task-param-grid {
		display: grid;
		grid-template-columns: minmax(120px, 220px) 1fr;
		gap: 0.35rem 0.75rem;
	}

	.task-param-key {
		font-size: 0.8rem;
		color: rgb(107, 114, 128);
		word-break: break-word;
	}

	.task-param-value {
		font-size: 0.84rem;
		word-break: break-word;
	}

	:global(.dark) .task-param-key {
		color: rgb(156, 163, 175);
	}

	@media (max-width: 960px) {
		.task-param-grid {
			grid-template-columns: 1fr;
			gap: 0.25rem;
		}

		.task-count-line {
			width: 100%;
			margin-left: 0;
		}
	}
</style>
