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
		type GenerationTaskPromptResourceItem,
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
	let customStartAt = '';
	let customEndAt = '';
	let includeDeleted = false;

	let showPreview = false;
	let selectedTask: GenerationTaskItem | null = null;
	let selectedTaskPromptSegments: PromptSegment[] = [];
	let selectedTaskParamEntries: Array<[string, string]> = [];

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
		{ value: 'custom', label: '自定义时间' }
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

	const datetimeLocalToEpoch = (value: string): number | undefined => {
		const text = String(value || '').trim();
		if (!text) return undefined;
		const ms = new Date(text).getTime();
		if (Number.isNaN(ms) || ms <= 0) return undefined;
		return Math.floor(ms / 1000);
	};

	const epochToDatetimeLocal = (epochSeconds: number): string => {
		const d = new Date(Number(epochSeconds) * 1000);
		if (Number.isNaN(d.getTime())) return '';
		const pad = (v: number) => String(v).padStart(2, '0');
		return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
	};

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
		if (selectedTimePreset === 'custom') {
			return {
				startAt: datetimeLocalToEpoch(customStartAt),
				endAt: datetimeLocalToEpoch(customEndAt)
			};
		}
		return {};
	};

	const isTimeRangeValid = (range: { startAt?: number; endAt?: number }): boolean => {
		if (selectedTimePreset !== 'custom') return true;
		if (range.startAt !== undefined && range.endAt !== undefined) {
			return range.startAt <= range.endAt;
		}
		return true;
	};

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
		includeDeleted = ['1', 'true', 'yes', 'on'].includes(
			String(query.get('include_deleted') || '')
				.trim()
				.toLowerCase()
		);

		const urlPreset = String(query.get('time_preset') || '').trim();
		const supportedPreset = TIME_PRESET_OPTIONS.find((item) => item.value === urlPreset);
		const urlStartAt = Number(query.get('start_at') || 0);
		const urlEndAt = Number(query.get('end_at') || 0);

		if (supportedPreset) {
			selectedTimePreset = supportedPreset.value;
		} else if (urlStartAt > 0 || urlEndAt > 0) {
			selectedTimePreset = 'custom';
		} else {
			selectedTimePreset = '7d';
		}

		customStartAt = urlStartAt > 0 ? epochToDatetimeLocal(urlStartAt) : '';
		customEndAt = urlEndAt > 0 ? epochToDatetimeLocal(urlEndAt) : '';
	};

	const writeFiltersToUrl = () => {
		if (typeof window === 'undefined') return;

		const query = new URLSearchParams();
		const range = resolveTimeRange();

		if (selectedUserId) query.set('user_id', selectedUserId);
		if (selectedGroupId) query.set('group_id', selectedGroupId);
		if (selectedProvider) query.set('provider', selectedProvider);
		if (selectedStatus) query.set('status', selectedStatus);
		if (includeDeleted) query.set('include_deleted', '1');
		query.set('time_preset', selectedTimePreset);
		if (selectedTimePreset === 'custom') {
			if (range.startAt !== undefined) query.set('start_at', String(range.startAt));
			if (range.endAt !== undefined) query.set('end_at', String(range.endAt));
		}

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
				include_deleted: includeDeleted,
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

	const queryTasks = async () => {
		const range = resolveTimeRange();
		if (!isTimeRangeValid(range)) {
			toast.error('开始时间不能晚于结束时间');
			return;
		}
		writeFiltersToUrl();
		await loadTaskProviders();
		await resetAndLoadTasks();
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
		const urlByRef = new Map(resources.map((item) => [String(item.name || '').trim(), item.url]));
		const regex = /@([^\s@,，。；;:：!！?？)）\]】}》>"“”'`]+)/g;
		const segments: PromptSegment[] = [];
		let lastIdx = 0;
		let match: RegExpExecArray | null = null;
		while ((match = regex.exec(prompt)) !== null) {
			const full = match[0];
			const ref = String(match[1] || '').trim();
			if (match.index > lastIdx) {
				segments.push({ text: prompt.slice(lastIdx, match.index) });
			}
			const mappedUrl = urlByRef.get(ref);
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
				pushIfPresent(key, value);
			});
			return rows;
		}

		pushIfPresent('model', task.model);
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

	const getValidPromptResources = (task: GenerationTaskItem): GenerationTaskPromptResourceItem[] =>
		(task.prompt_resources || []).filter(
			(item) => item.url.startsWith('http://') || item.url.startsWith('https://')
		);

	const copyText = async (text: string) => {
		if (navigator?.clipboard?.writeText) {
			await navigator.clipboard.writeText(text);
			return;
		}

		const textarea = document.createElement('textarea');
		textarea.value = text;
		textarea.style.position = 'fixed';
		textarea.style.opacity = '0';
		document.body.appendChild(textarea);
		textarea.focus();
		textarea.select();
		document.execCommand('copy');
		document.body.removeChild(textarea);
	};

	const copyPromptAndParams = async (task: GenerationTaskItem) => {
		const lines: string[] = [];
		lines.push(`任务ID: ${task.task_id}`);
		lines.push(`用户: ${task.user_name || task.user_id || '-'}`);
		lines.push(`状态: ${statusLabel(task.status)} / ${statusLabel(task.archive_status)}`);
		lines.push('');
		lines.push('提示词:');
		lines.push(task.prompt_text && String(task.prompt_text).trim() ? String(task.prompt_text) : '（空）');

		const params = getGenerationParamEntries(task);
		if (params.length > 0) {
			lines.push('');
			lines.push('生成参数:');
			params.forEach(([key, value]) => {
				lines.push(`- ${key}: ${value}`);
			});
		}

		const resources = getValidPromptResources(task);
		if (resources.length > 0) {
			lines.push('');
			lines.push('资源URL:');
			resources.forEach((item) => {
				lines.push(`- @${item.name}: ${item.url}`);
			});
		}

		try {
			await copyText(lines.join('\n'));
			toast.success('已复制提示词与参数');
		} catch (error) {
			toast.error(`复制失败: ${error}`);
		}
	};

	$: selectedTaskPromptSegments = selectedTask ? getPromptSegments(selectedTask) : [];
	$: selectedTaskParamEntries = selectedTask ? getGenerationParamEntries(selectedTask) : [];

	onMount(async () => {
		const defaultUserId = String($user?.id || '').trim();
		applyFiltersFromUrl(defaultUserId);
		await loadTaskUsers();
		await loadTaskGroups();
		await loadTaskProviders();
		writeFiltersToUrl();
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

				{#if selectedTimePreset === 'custom'}
					<input
						class="task-input"
						type="datetime-local"
						bind:value={customStartAt}
						on:keydown={(e) => {
							if (e.key === 'Enter') {
								queryTasks();
							}
						}}
					/>
					<input
						class="task-input"
						type="datetime-local"
						bind:value={customEndAt}
						on:keydown={(e) => {
							if (e.key === 'Enter') {
								queryTasks();
							}
						}}
					/>
				{/if}
			</div>

			<div class="task-filter-actions">
				<label class="task-checkbox-wrap">
					<input type="checkbox" bind:checked={includeDeleted} />
					<span>包含已删除</span>
				</label>

				<button class="task-btn" on:click={queryTasks}>查询</button>

				<button class="task-btn task-btn-icon" on:click={queryTasks} aria-label="refresh">
					<Refresh className="size-4" />
				</button>

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

					<button
						type="button"
						class="task-icon-btn modal-icon"
						on:click={() => {
							copyPromptAndParams(selectedTask);
						}}
						aria-label="copy"
					>
						<Clipboard className="size-4" />
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
				<div class="task-detail-title">提示词</div>
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

	:global(.dark) .task-detail-title {
		background: rgba(31, 41, 55, 0.65);
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
