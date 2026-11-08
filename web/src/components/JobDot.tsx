import { JOB_LEVEL, JOB_TEXT } from '../lib/format'
import type { JobStatus } from '../types'
import { StatusDot } from './StatusDot'

export function JobDot({ status }: { status: JobStatus }) {
  return <StatusDot level={JOB_LEVEL[status]} label={JOB_TEXT[status]} />
}
