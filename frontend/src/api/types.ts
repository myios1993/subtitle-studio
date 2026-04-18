/** Shared TypeScript types matching backend Pydantic schemas */

export interface Speaker {
  id: number
  project_id: number
  speaker_id: string
  label: string | null
  color: string | null
}

export interface Project {
  id: number
  name: string
  capture_mode: 'microphone' | 'loopback' | 'file'
  status: 'idle' | 'capturing' | 'processing' | 'done' | 'error'
  source_audio_path: string | null
  source_video_path: string | null
  playback_video_path: string | null
  source_language: string | null
  created_at: string
  updated_at: string
  speakers: Speaker[]
  segment_count: number
}

export interface ProjectListItem {
  id: number
  name: string
  capture_mode: string
  status: string
  source_language: string | null
  created_at: string
  updated_at: string
  segment_count: number
}

export interface Segment {
  id: number
  project_id: number
  sequence: number
  start_ms: number
  end_ms: number
  original_text: string
  translated_text: string | null
  original_language: string | null
  speaker_id: string | null
  is_manually_edited: boolean
  confidence: number | null
}

export interface AudioDevice {
  index: number
  name: string
  channels: number
  sample_rate: number
  type: 'microphone' | 'loopback'
}

export interface AudioDevices {
  microphones: AudioDevice[]
  loopbacks: AudioDevice[]
  default_loopback: AudioDevice | null
}

/** WebSocket event payload */
export interface WsEvent {
  event:
    | 'segment_created'
    | 'segment_updated'
    | 'pipeline_status'
    | 'pipeline_done'
    | 'pipeline_error'
    | 'diarization_done'
    | 'translation_started'
    | 'translation_progress'
    | 'translation_done'
    | 'translation_error'
    | 'translation_warning'
  data: any
}
