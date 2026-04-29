import { WEBUI_API_BASE_URL } from '$lib/constants';

export type MediaAssetItem = {
	asset_id: string;
	user_id: string;
	chat_id?: string | null;
	display_name: string;
	relative_path?: string | null;
	original_filename: string;
	media_type: 'image' | 'video' | 'audio' | string;
	mime_type?: string | null;
	size_bytes: number;
	status: string;
	tos_key?: string | null;
	tos_status?: string | null;
	tos_error?: string | null;
	created_at: number;
	updated_at: number;
};

export const listMediaAssets = async (
	token: string,
	params: {
		media_type?: string;
		status?: string;
		chat_id?: string;
		limit?: number;
		offset?: number;
	} = {}
): Promise<MediaAssetItem[]> => {
	const query = new URLSearchParams();
	if (params.media_type) query.append('media_type', params.media_type);
	if (params.status) query.append('status', params.status);
	if (params.chat_id) query.append('chat_id', params.chat_id);
	query.append('limit', String(params.limit ?? 100));
	query.append('offset', String(params.offset ?? 0));

	let error = null;
	const res = await fetch(`${WEBUI_API_BASE_URL}/media-assets/?${query.toString()}`, {
		method: 'GET',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			authorization: `Bearer ${token}`
		}
	})
		.then(async (resp) => {
			if (!resp.ok) throw await resp.json();
			return resp.json();
		})
		.catch((err) => {
			error = err?.detail || err?.message || 'Failed to list media assets';
			console.error(err);
			return [];
		});

	if (error) throw error;
	return Array.isArray(res) ? res : [];
};

export const createMediaAssetsFromUploads = async (
	token: string,
	payload: {
		upload_ids?: string[];
		chat_id?: string;
		uploads?: Array<{
			upload_id?: string;
			file_path?: string;
			relative_path?: string;
			original_filename?: string;
			mime_type?: string;
		}>;
	}
): Promise<{
	ok: boolean;
	uploaded: MediaAssetItem[];
	failed: Array<{ filename: string; error: string }>;
	count: number;
}> => {
	let error = null;
	const res = await fetch(`${WEBUI_API_BASE_URL}/media-assets/from-upload`, {
		method: 'POST',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			authorization: `Bearer ${token}`
		},
		body: JSON.stringify(payload)
	})
		.then(async (resp) => {
			if (!resp.ok) throw await resp.json();
			return resp.json();
		})
		.catch((err) => {
			error = err?.detail || err?.message || 'Failed to create media assets';
			console.error(err);
			return null;
		});

	if (error) throw error;
	return res;
};
