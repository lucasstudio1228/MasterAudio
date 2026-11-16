import { useState } from 'react';
import type { MixSetCreate, Project, ProjectCreate, ProjectKind } from '../../types';
import MusicLineSelect from '../shared/MusicLineSelect';

interface Props {
  open: boolean;
  initial?: Project;
  saving?: boolean;
  /** Đang gửi yêu cầu tạo lô (AI sinh ý tưởng có thể mất vài giây). */
  batchSaving?: boolean;
  batchError?: string | null;
  /** Project tạo tay luôn dừng ở trạng thái sẵn sàng; Mix Set dùng endpoint tự động riêng. */
  /** ownCovers = project «từ bài cover có sẵn» → mở thẳng chỗ kéo thả file cover. */
  onSubmit: (data: ProjectCreate, opts: { autoRun: boolean; ownCovers?: boolean }) => void;
  /** Bỏ qua → không hiện chế độ Mix Set (vd khi đang sửa project).
   *  autoRun=false → «Chỉ tạo, tôi tự chạy»: tạo sẵn project + bài, không chạy gì. */
  onSubmitBatch?: (data: MixSetCreate, opts: { autoRun: boolean }) => void;
  onClose: () => void;
}

const norm = (s: string) => s.trim().toLowerCase();
const genreNorm = (s: string) => {
  const value = norm(s).replace(/[-_]+/g, ' ').replace(/\s+/g, ' ');
  if (['mix việt', 'mix viet', 'viet mix'].includes(value)) return 'vietmix';
  if (['rnb', 'rhythm and blues'].includes(value)) return 'r&b';
  if (value === 'lo fi') return 'lofi';
  return value;
};
const CUSTOM = '__custom__';

/** File danh sách bài nhận được (Word, Excel, chữ thường, CSV). */
const LIST_EXTS = ['.txt', '.docx', '.doc', '.xlsx', '.xlsm', '.xls', '.csv'];
const LIST_ACCEPT = LIST_EXTS.join(',');

/** Catalog thể loại chuẩn của tool Cover. Lưu vào music_style — đầu vào
 *  profile BPM/groove/harmony/arrangement cho Suno. */
const COVER_GENRES = [
  'Ballad', 'Pop', 'R&B', 'Rock', 'Acoustic', 'Jazz', 'Blues', 'Lofi',
  'House', 'Deep House', 'Future House', 'Tropical House',
  'Vietmix', 'Dance Remix',
];

/** Chọn thể loại: danh sách cố định + «Khác (tự nhập)». */
function GenrePicker({
  value,
  onChange,
  className,
}: {
  value: string;
  onChange: (v: string) => void;
  className: string;
}) {
  const known = new Set(COVER_GENRES.map(genreNorm));
  const [custom, setCustom] = useState(Boolean(value) && !known.has(genreNorm(value)));
  const match = COVER_GENRES.find((g) => genreNorm(g) === genreNorm(value));
  return (
    <>
      <select
        className={className}
        value={custom ? CUSTOM : (match ?? '')}
        onChange={(e) => {
          if (e.target.value === CUSTOM) {
            setCustom(true);
            onChange('');
          } else {
            setCustom(false);
            onChange(e.target.value);
          }
        }}
      >
        <option value="">— chọn thể loại —</option>
        {COVER_GENRES.map((g) => (
          <option key={g} value={g}>{g}</option>
        ))}
        <option value={CUSTOM}>Khác (tự nhập)…</option>
      </select>
      {custom && (
        <input
          className={`${className} mt-2`}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder="vd: Bolero, EDM, Acoustic, Indie…"
          autoFocus
        />
      )}
    </>
  );
}

export default function ProjectFormModal({
  open,
  initial,
  saving,
  batchSaving,
  batchError,
  onSubmit,
  onSubmitBatch,
  onClose,
}: Props) {
  const [name, setName] = useState(initial?.name ?? '');
  const [description, setDescription] = useState(initial?.description ?? '');
  // Nhạc cụ + thể loại của project đơn lẻ (chung state với chế độ hàng loạt).

  // ── Tạo từ danh sách: '' = tạo tay 1 project; 'mixset' = cả danh sách → 1
  //    project Mix Set; 'single' = mỗi bài → 1 project Track lẻ ──
  const [listMode, setListMode] = useState<'' | ProjectKind>('');
  // «📂 Project từ bài cover có sẵn»: tạo 1 project rồi mở chỗ kéo thả file cover.
  const [ownMode, setOwnMode] = useState(false);
  // Tạo từ danh sách: true = tạo & chạy tự động; false = chỉ tạo, tôi tự chạy.
  const [runAuto, setRunAuto] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const batchMode = listMode !== '';
  const singles = listMode === 'single';
  const listLabel = singles ? 'Track lẻ' : 'Mix Set';
  // Nhóm (thư mục media/Mix Set | media/Track lẻ) của project tạo tay / đang sửa.
  const [kind, setKind] = useState<ProjectKind>(initial?.kind ?? 'mixset');
  const [songFile, setSongFile] = useState<File | null>(null);
  const [songLineCount, setSongLineCount] = useState<number | null>(null);
  const [songFileError, setSongFileError] = useState('');
  const [instrument] = useState(initial?.instrument ?? '');
  const [musicStyle, setMusicStyle] = useState(initial?.music_style ?? '');
  // Dòng nhạc (VN / US-UK / Chinese…).
  const [musicLine, setMusicLine] = useState(initial?.music_line ?? '');
  const [theme, setTheme] = useState('');
  // Mix Set: AI tự chọn thêm 1 nhạc cụ nổi bật cho từng bài Suno (mặc định bật).
  const [creativeInstrument, setCreativeInstrument] = useState(
    initial?.creative_instrument ?? true
  );
  const [confirmed, setConfirmed] = useState(false);
  if (!open) return null;

  const isEdit = Boolean(initial);
  const canBatch = !isEdit && Boolean(onSubmitBatch);
  const totalCredits = (songLineCount ?? 0) * 2;
  const instrumentChanged = isEdit && instrument.trim() !== (initial?.instrument ?? '');
  const creativeChanged = isEdit && creativeInstrument !== (initial?.creative_instrument ?? true);
  const kindChanged = isEdit && kind !== (initial?.kind ?? 'mixset');
  function submit(e: React.FormEvent) {
    e.preventDefault();
    if (batchMode) {
      if (!batchReady || !onSubmitBatch) return;
      onSubmitBatch({
        kind: listMode,
        name: singles ? '' : name.trim(),
        song_file: songFile!,
        music_style: musicStyle.trim(),
        music_line: musicLine.trim(),
        theme: theme.trim(),
        creative_instrument: creativeInstrument,
        audio_only: true,
      }, { autoRun: runAuto });
      return;
    }
    if (!name.trim() || (!isEdit && !ownMode && !musicStyle.trim())) return;
    const data: ProjectCreate = { name: name.trim(), description };
    // Sửa project: chỉ gửi nhạc cụ/thể loại khi đổi (giữ chữ ký hồ sơ prompt cũ).
    if (!isEdit || instrumentChanged) data.instrument = instrument.trim();
    if (!isEdit || musicStyle.trim() !== (initial?.music_style ?? ''))
      data.music_style = musicStyle.trim();
    if (!isEdit || musicLine.trim() !== (initial?.music_line ?? ''))
      data.music_line = musicLine.trim();
    if (!isEdit || creativeChanged) data.creative_instrument = creativeInstrument;
    if (!isEdit || kindChanged) data.kind = kind;
    onSubmit(data, { autoRun: false, ownCovers: ownMode });
  }

  const inputCls =
    'w-full rounded-lg border border-white/10 bg-background px-3 py-2 text-sm text-white outline-none focus:border-accent';
  const labelCls = 'mb-1 block text-xs font-medium text-gray-400';

  const batchReady = Boolean(
    (singles || name.trim()) && musicStyle.trim() && songFile && !songFileError && (confirmed || !runAuto)
  );

  async function chooseSongFile(file: File | null) {
    setSongFile(file);
    setSongLineCount(null);
    setSongFileError('');
    if (!file) return;
    const lower = file.name.toLowerCase();
    if (!LIST_EXTS.some((ext) => lower.endsWith(ext))) {
      setSongFileError('Chỉ nhận file Word (.docx, .doc), Excel (.xlsx, .xls), .txt hoặc .csv.');
      return;
    }
    if (lower.endsWith('.txt')) {
      const lines = (await file.text()).split(/\r?\n/).map((v) => v.trim()).filter(Boolean);
      const invalid = lines.find((v) => !/\s[-–—]\s/.test(v));
      if (!lines.length) setSongFileError('File TXT chưa có bài hát.');
      else if (invalid) setSongFileError(`Dòng sai định dạng: “${invalid}”`);
      else if (lines.length > 50) setSongFileError(`Tối đa 50 bài (file có ${lines.length} dòng).`);
      else setSongLineCount(lines.length);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <form
        onSubmit={submit}
        className="max-h-[90vh] w-full max-w-lg overflow-y-auto rounded-xl bg-surface p-6 shadow-xl"
      >
        <h3 className="mb-4 text-lg font-semibold text-white">
          {isEdit ? 'Edit Project' : 'New Project'}
        </h3>

        {canBatch && (
          <div className="mb-4 space-y-1.5 rounded-lg border border-white/10 bg-background px-3 py-2 text-sm text-gray-200">
            {([
              ['', '✍️ Tạo 1 project thủ công (trống)'],
              ['own', '📂 Project từ bài cover có sẵn — kéo thả file cover, tự master + ghép lời'],
              ['mixset', '🎼 Mix Set — cả danh sách bài ghép thành 1 bản mix dài'],
              ['single', '🎵 Track lẻ — mỗi bài trong danh sách thành 1 project riêng'],
            ] as const).map(([value, label]) => (
              <label key={value} className="flex items-start gap-2">
                <input
                  type="radio"
                  name="create-mode"
                  className="mt-1"
                  checked={value === 'own' ? ownMode : !ownMode && listMode === value}
                  onChange={() => {
                    setOwnMode(value === 'own');
                    setListMode(value === 'own' ? '' : value);
                    setConfirmed(false);
                  }}
                />
                <span>{label}</span>
              </label>
            ))}
          </div>
        )}

        {!batchMode ? (
          <>
            <label className={labelCls}>Name *</label>
            <input
              className={`${inputCls} mb-4`}
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="vd: Tuyển tập Ballad Việt hay nhất"
              autoFocus
            />

            <label className={labelCls}>Description</label>
            <textarea
              className={`${inputCls} mb-4`}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={2}
              placeholder="Không khí, bối cảnh, câu chuyện — AI bám vào đây khi viết prompt"
            />

            <div className="mb-4">
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className={labelCls}>Dòng nhạc</label>
                  <MusicLineSelect
                    value={musicLine}
                    onChange={setMusicLine}
                    className={inputCls}
                    emptyLabel="— chưa chọn —"
                    title="Dòng nhạc của bài"
                  />
                </div>
                <div>
                  <label className={labelCls}>Thể loại nhạc {isEdit || ownMode ? '' : '*'}</label>
                  <GenrePicker
                    value={musicStyle}
                    onChange={setMusicStyle}
                    className={inputCls}
                  />
                </div>
              </div>
              <p className="mt-1 text-xs text-gray-500">
                Thể loại quyết định nhịp, phối khí và không khí của bản cover trên Suno.
              </p>
            </div>
            <div className="mb-4">
              <label className={labelCls}>Nhóm (thư mục lưu)</label>
              <select
                className={inputCls}
                value={kind}
                onChange={(e) => setKind(e.target.value as ProjectKind)}
              >
                <option value="mixset">🎼 Mix Set (nhiều bài ghép 1 bản mix)</option>
                <option value="single">🎵 Track lẻ (mỗi bài 1 project)</option>
              </select>
              <p className="mt-1 text-xs text-gray-500">
                {isEdit
                  ? <>Project tạo trước bản cập nhật này vẫn nằm thẳng trong <b>media/</b>; đổi
                      nhóm thì thư mục được chuyển vào <b>media/{kind === 'single' ? 'Track lẻ' : 'Mix Set'}/</b>.</>
                  : <>File của project sẽ nằm trong <b>media/{kind === 'single' ? 'Track lẻ' : 'Mix Set'}/</b>.</>}
                {kindChanged && (
                  <span className="text-amber-300/80">
                    {' '}Khi lưu, thư mục của project được chuyển sang nhóm mới (cần project
                    đang rảnh, đóng các file của project đang mở).
                  </span>
                )}
              </p>
            </div>
            <label className="mb-4 flex items-start gap-2 rounded-lg border border-purple-500/30 bg-purple-500/10 px-3 py-2 text-xs text-gray-200">
              <input
                type="checkbox"
                className="mt-0.5"
                checked={creativeInstrument}
                onChange={(e) => setCreativeInstrument(e.target.checked)}
              />
              <span>
                <b>Thêm nhạc cụ sáng tạo</b> — mặc định bật để AI tự chọn nhạc cụ phù hợp
                cho từng bài. Bỏ tick để không thêm; vẫn chỉnh riêng từng bài ở tab Suno Cover.
              </span>
            </label>
            {!isEdit && ownMode && (
              <p className="mb-4 rounded-lg border border-emerald-500/25 bg-emerald-500/10 px-3 py-2 text-xs text-emerald-100">
                Tạo xong, tool mở tab <b>② Suno Cover</b> ở ô <b>📂 Kéo thả bài cover có sẵn</b>:
                kéo các file nhạc cover (và file lời .txt/.lrc nếu có) vào. Tool tự <b>master +
                đồng bộ âm lượng</b> từng bài và <b>AI căn lời</b> để xuất file lời <b>.srt</b>.
                Không tải bài gốc, không dùng Suno. Thể loại để trống cũng được.
              </p>
            )}
            {!isEdit && !ownMode && (
              <p className="mb-4 rounded-lg border border-cyan-500/25 bg-cyan-500/10 px-3 py-2 text-xs text-cyan-100">
                Project thủ công chỉ được tạo và chuẩn bị đầy đủ công cụ. Bạn chủ động chạy từng
                bước; khi cần vẫn có thể dùng nút <b>🎧 Chạy tự động</b> ở thanh dưới cùng.
              </p>
            )}
          </>
        ) : (
          <div className="mb-4 space-y-3">
            <div className="space-y-1.5 rounded-lg border border-white/10 bg-background px-3 py-2 text-sm text-gray-200">
              <label className="flex items-start gap-2">
                <input
                  type="radio"
                  name="run-mode"
                  className="mt-1"
                  checked={!runAuto}
                  onChange={() => { setRunAuto(false); setConfirmed(false); }}
                />
                <span>
                  ✍️ <b>Chỉ tạo, tôi tự chạy</b>
                  <span className="block text-xs text-gray-500">
                    Tạo sẵn project và danh sách bài, <b>chưa chạy gì, chưa tốn credit</b>. Bạn
                    tự bấm từng bước, hoặc bấm 🎧 Chạy tự động khi muốn.
                  </span>
                </span>
              </label>
              <label className="flex items-start gap-2">
                <input
                  type="radio"
                  name="run-mode"
                  className="mt-1"
                  checked={runAuto}
                  onChange={() => { setRunAuto(true); setConfirmed(false); }}
                />
                <span>
                  🎧 <b>Tạo & chạy tự động</b>
                  <span className="block text-xs text-gray-500">
                    Tool tự làm hết từng bài ngay sau khi tạo (dùng credit Suno).
                  </span>
                </span>
              </label>
            </div>
            {singles ? (
              <p className="rounded-lg border border-white/10 bg-background px-3 py-2 text-xs text-gray-400">
                Mỗi dòng trong file thành <b>một project Track lẻ riêng</b> (tên project = dòng
                đó), lưu trong thư mục <b>media/Track lẻ/</b>. Khi chạy, tool làm lần lượt từng
                bài: tải bài gốc → tempo 4.5× → Suno Cover → master. Bài nào lỗi thì bỏ qua,
                làm tiếp bài sau.
              </p>
            ) : (
              <p className="rounded-lg border border-white/10 bg-background px-3 py-2 text-xs text-gray-400">
                Tạo đúng <b>một project Mix Set</b> (thư mục <b>media/Mix Set/</b>). Mỗi dòng
                trong file là một bài theo dạng <b>Tên bài - Ca sĩ</b> hoặc <b>Ca sĩ - Tên
                bài</b>. Khi chạy, tool lần lượt tải bài gốc → tempo 4.5× → Suno Cover → master →
                ghép mix.
              </p>
            )}

            {!singles && (
              <div>
                <label className={labelCls}>Tên Mix Set *</label>
                <input
                  className={inputCls}
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="vd: Ballad nhạc Việt mùa Thu"
                />
              </div>
            )}

            <div>
              <label className={labelCls}>Danh sách bài hát (Word, Excel hoặc .txt) *</label>
              <label
                onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
                onDragLeave={() => setDragOver(false)}
                onDrop={(e) => {
                  e.preventDefault();
                  setDragOver(false);
                  void chooseSongFile(e.dataTransfer.files?.[0] ?? null);
                }}
                className={`flex cursor-pointer flex-col items-center justify-center rounded-lg border-2 border-dashed px-3 py-4 text-center text-xs ${
                  dragOver ? 'border-accent bg-accent/10 text-white' : 'border-white/15 bg-background text-gray-400'
                }`}
              >
                <span className="text-2xl">📄</span>
                {songFile
                  ? <span className="mt-1 text-sm text-white">{songFile.name}</span>
                  : <span className="mt-1">Kéo thả file danh sách vào đây, hoặc bấm để chọn file</span>}
                <span className="mt-0.5 text-gray-500">.docx · .doc · .xlsx · .xls · .txt · .csv</span>
                <input
                  type="file"
                  accept={LIST_ACCEPT}
                  className="hidden"
                  onChange={(e) => void chooseSongFile(e.target.files?.[0] ?? null)}
                />
              </label>
              <p className="mt-1 text-xs text-gray-500">
                Word / .txt: mỗi dòng 1 bài <b>Tên bài - Ca sĩ</b>. Excel: mỗi hàng 1 bài — cột
                Tên bài và cột Ca sĩ (hoặc 1 cột ghi sẵn «Tên bài - Ca sĩ»); hàng tiêu đề và
                cột STT tự bỏ qua. Tối đa 50 bài.
                {songLineCount != null ? ` Đã đọc ${songLineCount} bài.` : ''}
              </p>
              {songFileError && <p className="mt-1 text-xs text-red-300">{songFileError}</p>}
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className={labelCls}>Dòng nhạc</label>
                <MusicLineSelect
                  value={musicLine}
                  onChange={setMusicLine}
                  className={inputCls}
                  emptyLabel="— chưa chọn —"
                  title={`Dòng nhạc của ${listLabel}`}
                />
              </div>
              <div>
                <label className={labelCls}>Thể loại nhạc *</label>
                <GenrePicker
                  value={musicStyle}
                  onChange={setMusicStyle}
                  className={inputCls}
                />
              </div>
            </div>

            <div>
              <label className={labelCls}>
                {singles
                  ? 'Chủ đề chung (tuỳ chọn — để trống thì AI tự hiểu theo tên từng bài)'
                  : 'Chủ đề (tuỳ chọn — để trống thì AI tự chọn 1 mạch chung)'}
              </label>
              <input
                className={inputCls}
                value={theme}
                onChange={(e) => setTheme(e.target.value)}
                placeholder="vd: tình đầu tuổi học trò, mưa đêm, chia tay…"
              />
            </div>

            <div>
              <label className="flex items-start gap-2 text-sm text-gray-200">
                <input
                  type="checkbox"
                  className="mt-0.5"
                  checked={creativeInstrument}
                  onChange={(e) => setCreativeInstrument(e.target.checked)}
                />
                <span>
                  Tự động chọn thêm nhạc cụ để bài hát sáng tạo hơn
                  <span className="mt-0.5 block text-xs text-gray-500">
                    AI chọn 1 nhạc cụ hợp thể loại (vd sáo, saxophone, guitar điện…) cho từng bài
                    khi làm cover trên Suno. Vẫn đổi riêng từng bài được ở tab ② Suno Cover.
                  </span>
                </span>
              </label>
            </div>

            {runAuto && <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2.5">
              <p className="text-sm font-medium text-amber-200">
                ⚠️ Đây là quy trình LIVE, sẽ dùng credit Suno cho từng bài
                {songLineCount != null ? ` (khoảng ${totalCredits} bản cover đầu ra)` : ''}.
              </p>
              <p className="mt-1 text-xs text-amber-200/80">
                Tool xử lý tuần tự và <b>không</b> tự mua thêm credit. Khi thiếu đăng nhập,
                credit hoặc thao tác xác nhận, {listLabel} dừng an toàn để bạn tiếp tục.
              </p>
              <label className="mt-2 flex items-center gap-2 text-xs text-amber-100">
                <input
                  type="checkbox"
                  checked={confirmed}
                  onChange={(e) => setConfirmed(e.target.checked)}
                />
                Tôi hiểu và xác nhận chạy {singles
                  ? `${songLineCount ?? 'các'} Track lẻ này`
                  : 'Mix Set này'}.
              </label>
            </div>}

            {batchError && (
              <p role="alert" className="text-sm text-red-300">
                {batchError}
              </p>
            )}
          </div>
        )}

        <div className="flex justify-end gap-3">
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg px-4 py-2 text-sm text-gray-300 hover:bg-surface-2"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={
              batchMode
                ? batchSaving || !batchReady
                : saving || !name.trim() || (!isEdit && !ownMode && !musicStyle.trim())
            }
            className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white hover:bg-accent-hover disabled:opacity-50"
          >
            {batchMode
              ? batchSaving
                ? `Đang tạo ${listLabel}…`
                : !runAuto
                  ? singles ? '🎵 Tạo Track lẻ (chưa chạy)' : '🎼 Tạo Mix Set (chưa chạy)'
                  : singles
                    ? `🎵 Tạo & chạy ${songLineCount ?? ''} Track lẻ`
                    : `🎼 Tạo & chạy Mix Set`
              : isEdit
                ? 'Save Changes'
                : ownMode ? '📂 Tạo & mở chỗ kéo thả' : 'Create Project'}
          </button>
        </div>
      </form>
    </div>
  );
}
