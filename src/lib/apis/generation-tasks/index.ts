import { WEBUI_API_BASE_URL } from '$lib/constants';

export type GenerationTaskItem = {
	task_id: string;
	user_id?: string | null;
	user_name?: string | null;
	package_id?: string | null;
	chat_id?: string | null;
	model?: string | null;
	status?: string | null;
	archive_status?: string | null;
	archive_error?: string | null;
	archive_retry_count?: number | null;
	archive_updated_at?: number | null;
	download_ready: boolean;
	can_delete: boolean;
	deleted_at?: number | null;
	created_at: number;
	updated_at: number;
	references?: string[];
	duration?: number | null;
	ratio?: string | null;
	watermark?: boolean | null;
	generate_audio?: boolean | null;
	thumbnail_url?: string | null;
	video_preview_url?: string | null;
	video_download_url?: string | null;
	video_url?: string | null;
	error_code?: string | null;
	error_message?: string | null;
	request_id?: string | null;
};

type UnifiedTaskApiItem = {
	id: string;
	provider?: string;
	provider_task_id?: string;
	tool_name?: string;
	skill_name?: string | null;
	user_id?: string | null;
	user_name?: string | null;
	chat_id?: string | null;
	model?: string | null;
	status?: string | null;
	archive_status?: string | null;
	progress?: number | null;
	download_ready?: boolean;
	can_delete?: boolean;
	can_cancel?: boolean;
	error_code?: string | null;
	error_message?: string | null;
	request_id?: string | null;
	deleted_at?: number | null;
	created_at?: number;
	updated_at?: number;
	finished_at?: number | null;
	thumbnail_url?: string | null;
	video_preview_url?: string | null;
	video_download_url?: string | null;
};

type UnifiedTaskListResponse = {
	items: UnifiedTaskApiItem[];
	total: number;
	offset: number;
	limit: number;
};

export type GenerationTaskUserItem = {
	user_id: string;
	user_name: string;
};

export type GenerationTaskProviderResponse = {
	providers: string[];
};

export type GenerationTaskPreview = {
	ok: boolean;
	task_id: string;
	status?: string | null;
	archive_status?: string | null;
	download_ready: boolean;
	can_delete: boolean;
	thumbnail_url?: string | null;
	video_preview_url?: string | null;
};

const buildAuthHeaders = (token: string): HeadersInit => ({
	Accept: 'application/json',
	'Content-Type': 'application/json',
	authorization: `Bearer ${token}`
});

const mapUnifiedTaskToLegacy = (item: UnifiedTaskApiItem): GenerationTaskItem => {
	const taskId = item.id || item.provider_task_id || '';
	return {
		task_id: taskId,
		user_id: item.user_id ?? null,
		user_name: item.user_name ?? null,
		package_id: null,
		chat_id: item.chat_id ?? null,
		model: item.model ?? null,
		status: item.status ?? null,
		archive_status: item.archive_status ?? null,
		archive_error: null,
		archive_retry_count: null,
		archive_updated_at: null,
		download_ready: Boolean(item.download_ready),
		can_delete: Boolean(item.can_delete),
		deleted_at: item.deleted_at ?? null,
		created_at: item.created_at ?? 0,
		updated_at: item.updated_at ?? 0,
		references: [],
		duration: null,
		ratio: null,
		watermark: null,
		generate_audio: null,
		thumbnail_url: item.thumbnail_url ?? null,
		video_preview_url: item.video_preview_url ?? null,
		video_download_url:
			item.video_download_url ?? `/api/v1/tasks/${encodeURIComponent(taskId)}/download`,
		video_url: null,
		error_code: item.error_code ?? null,
		error_message: item.error_message ?? null,
		request_id: item.request_id ?? null
	};
};

export const listGenerationTasks = async (
	token: string,
	params: {
		user_id?: string;
		provider?: string;
		skill_name?: string;
		tool_name?: string;
		status?: string;
		model?: string;
		chat_id?: string;
		package_id?: string;
		include_deleted?: boolean;
		refresh_status?: boolean;
		offset?: number;
		limit?: number;
	} = {}
): Promise<GenerationTaskItem[]> => {
	const query = new URLSearchParams();
	if (params.user_id) query.append('user_id', params.user_id);
	if (params.provider) query.append('provider', params.provider);
	if (params.skill_name) query.append('skill_name', params.skill_name);
	if (params.tool_name) query.append('tool_name', params.tool_name);
	if (params.status) query.append('status', params.status);
	if (params.model) query.append('model', params.model);
	if (params.chat_id) query.append('chat_id', params.chat_id);
	if (params.package_id) query.append('package_id', params.package_id);
	if (params.include_deleted !== undefined) {
		query.append('include_deleted', String(params.include_deleted));
	}
	if (params.refresh_status !== undefined) {
		query.append('refresh_status', String(params.refresh_status));
	}
	query.append('offset', String(params.offset ?? 0));
	query.append('limit', String(params.limit ?? 50));

	let error = null;
	const res = await fetch(`${WEBUI_API_BASE_URL}/tasks?${query.toString()}`, {
		method: 'GET',
		headers: buildAuthHeaders(token)
	})
		.then(async (resp) => {
			if (!resp.ok) throw await resp.json();
			return resp.json();
		})
		.catch((err) => {
			error = err?.detail || err?.message || 'Failed to list generation tasks';
			console.error(err);
			return [];
		});

	if (error) throw error;
	if (Array.isArray(res)) {
		return res;
	}
	if (res && Array.isArray((res as UnifiedTaskListResponse).items)) {
		return (res as UnifiedTaskListResponse).items.map(mapUnifiedTaskToLegacy);
	}
	return [];
};

export const listGenerationTaskUsers = async (token: string): Promise<GenerationTaskUserItem[]> => {
	let error = null;
	const res = await fetch(`${WEBUI_API_BASE_URL}/tasks/users`, {
		method: 'GET',
		headers: buildAuthHeaders(token)
	})
		.then(async (resp) => {
			if (!resp.ok) throw await resp.json();
			return resp.json();
		})
		.catch((err) => {
			error = err?.detail || err?.message || 'Failed to list generation task users';
			console.error(err);
			return [];
		});

	if (error) throw error;
	if (Array.isArray(res)) {
		return res;
	}
	if (res && Array.isArray((res as { users?: GenerationTaskUserItem[] }).users)) {
		return (res as { users: GenerationTaskUserItem[] }).users;
	}
	return [];
};

export const listGenerationTaskProviders = async (
	token: string,
	userId?: string
): Promise<string[]> => {
	const query = new URLSearchParams();
	if (userId) query.append('user_id', userId);
	query.append('include_deleted', 'false');

	let error = null;
	const res = await fetch(`${WEBUI_API_BASE_URL}/tasks/providers?${query.toString()}`, {
		method: 'GET',
		headers: buildAuthHeaders(token)
	})
		.then(async (resp) => {
			if (!resp.ok) throw await resp.json();
			return resp.json();
		})
		.catch((err) => {
			error = err?.detail || err?.message || 'Failed to list generation task providers';
			console.error(err);
			return null;
		});

	if (error) throw error;
	if (res && Array.isArray((res as GenerationTaskProviderResponse).providers)) {
		return (res as GenerationTaskProviderResponse).providers;
	}
	if (Array.isArray(res)) {
		return res as string[];
	}
	return [];
};

export const getGenerationTaskPreview = async (
	token: string,
	taskId: string
): Promise<GenerationTaskPreview> => {
	let error = null;
	const res = await fetch(`${WEBUI_API_BASE_URL}/tasks/${encodeURIComponent(taskId)}/preview`, {
		method: 'GET',
		headers: buildAuthHeaders(token)
	})
		.then(async (resp) => {
			if (!resp.ok) throw await resp.json();
			return resp.json();
		})
		.catch((err) => {
			error = err?.detail || err?.message || 'Failed to get task preview';
			console.error(err);
			return null;
		});

	if (error) throw error;
	return res;
};

export const deleteGenerationTask = async (
	token: string,
	taskId: string,
	deleteReason?: string
): Promise<{ ok: boolean; task_id: string; deleted_at: number }> => {
	const query = new URLSearchParams();
	if (deleteReason) query.append('delete_reason', deleteReason);

	let error = null;
	const res = await fetch(`${WEBUI_API_BASE_URL}/tasks/${encodeURIComponent(taskId)}?${query.toString()}`, {
		method: 'DELETE',
		headers: buildAuthHeaders(token)
	})
		.then(async (resp) => {
			if (!resp.ok) throw await resp.json();
			return resp.json();
		})
		.catch((err) => {
			error = err?.detail || err?.message || 'Failed to delete task';
			console.error(err);
			return null;
		});

	if (error) throw error;
	return res;
};

export const downloadGenerationTask = async (token: string, taskId: string): Promise<Blob> => {
	const resp = await fetch(`${WEBUI_API_BASE_URL}/tasks/${encodeURIComponent(taskId)}/download`, {
		method: 'GET',
		headers: {
			authorization: `Bearer ${token}`
		}
	});

	if (!resp.ok) {
		let detail = 'Failed to download task';
		try {
			const body = await resp.json();
			detail = body?.detail || body?.message || detail;
		} catch (e) {
			// Keep default detail.
		}
		throw detail;
	}

	return resp.blob();
};
