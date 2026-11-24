/** API error carrying the backend's `{"error": {"code", "message", ...}}` fields. */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly fields: { location: string; message: string }[];

  constructor(status: number, code: string, message: string, fields: { location: string; message: string }[] = []) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.fields = fields;
  }
}
