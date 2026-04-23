from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Deque, Dict, List, Optional, Set, Tuple

# 假設既有名稱仍存在：
# - NurseInfo
# - HolidayShiftSlot
# - HolidayShiftResult
# - get_holiday_shift_slots_for_month
# - get_weekend_key
# - check_should_skip_holiday_shift
# - get_holiday_compensation_for_slot

SLOT_ORDER_RANK = {
    'sat_day': 1,
    'sat_small_night': 2,
    'sun_night': 3,
    'sun_day': 4,
    'sun_small_night': 5,
    'weekday_holiday_day': 6,
}


@dataclass
class PendingHolidayMakeupTask:
    nurse_idx: int
    nurse_name: str
    reserved_column: int
    original_slot: Optional['HolidayShiftSlot'] = None
    original_text: str = ''
    adjust_type: Optional[str] = None
    source: str = 'current'   # current | prev_month
    column_hint: Optional[int] = None


@dataclass
class HolidaySeedState:
    seed_results: List['HolidayShiftResult'] = field(default_factory=list)
    occupied_slots: Set[Tuple[datetime.date, str]] = field(default_factory=set)
    next_col_by_nurse: Dict[str, int] = field(default_factory=dict)
    used_cols_by_nurse: Dict[str, Set[int]] = field(default_factory=dict)
    pending_by_nurse: Dict[str, Deque[PendingHolidayMakeupTask]] = field(
        default_factory=lambda: defaultdict(deque)
    )


def _slot_key(slot: 'HolidayShiftSlot') -> Tuple[datetime.date, str]:
    return (slot.date.date(), slot.shift_type_id)


def _slot_sort_key(slot: 'HolidayShiftSlot') -> Tuple[datetime, int]:
    return (slot.date, SLOT_ORDER_RANK.get(slot.shift_type_id, 999))


def _placeholder_slot(target_year: int, target_month: int) -> 'HolidayShiftSlot':
    """
    unresolved pending 用的虛擬 slot；真正顯示文字由 display_override 決定。
    """
    return HolidayShiftSlot(
        date=datetime(target_year, target_month, 1),
        shift_type_id='weekday_holiday_day',
        shift_name='白班',
        weekday=0,
        weekend_key=f'{target_year}-{target_month:02d}-placeholder',
        is_next_month=False,
    )


def _reserve_column(
    state: HolidaySeedState,
    nurse_name: str,
    preferred: Optional[int] = None,
) -> int:
    """
    欄位保留器：
    - 若有 preferred 且未被使用，優先保留該欄
    - 否則取此人的 next free column
    """
    used = state.used_cols_by_nurse.setdefault(nurse_name, set())
    state.next_col_by_nurse.setdefault(nurse_name, 1)

    if preferred and preferred > 0 and preferred not in used:
        used.add(preferred)
        state.next_col_by_nurse[nurse_name] = max(
            state.next_col_by_nurse[nurse_name],
            preferred + 1,
        )
        return preferred

    col = state.next_col_by_nurse[nurse_name]
    while col in used:
        col += 1

    used.add(col)
    state.next_col_by_nurse[nurse_name] = col + 1
    return col


def build_holiday_seed_state(
    nurses: List['NurseInfo'],
    target_year: int,
    target_month: int,
) -> HolidaySeedState:
    """
    由護士身上的 previous_month_cross_month_makeup_holidays / pending_holiday_makeup
    建立 seed state。
    """
    state = HolidaySeedState()
    nurse_idx_map = {n.name: i for i, n in enumerate(nurses)}

    for nurse in nurses:
        state.next_col_by_nurse.setdefault(nurse.name, 1)
        state.used_cols_by_nurse.setdefault(nurse.name, set())
        state.pending_by_nurse.setdefault(nurse.name, deque())

    fixed_results: List['HolidayShiftResult'] = []

    for nurse in nurses:
        # 1) 跨月固定班：建立 seed_results + occupied_slots
        for record in getattr(nurse, 'previous_month_cross_month_makeup_holidays', []) or []:
            if record.get('type') == 'pending':
                continue

            column = _reserve_column(state, nurse.name, record.get('column_hint'))
            dt = record['date']
            slot = HolidayShiftSlot(
                date=dt,
                shift_type_id=record['slot_type'],
                shift_name=record['shift_type'],
                weekday=dt.weekday(),
                weekend_key=get_weekend_key(dt),
                is_next_month=False,
            )
            fixed_results.append(
                HolidayShiftResult(
                    slot=slot,
                    nurse=nurse,
                    column_index=column,
                    is_cross_month_makeup=True,
                )
            )
            state.occupied_slots.add(_slot_key(slot))

        # 2) 上月帶來的 X月補：只 reserve 欄位，不立刻占用 slot
        for record in getattr(nurse, 'pending_holiday_makeup', []) or []:
            column = _reserve_column(state, nurse.name, record.get('column_hint'))
            state.pending_by_nurse[nurse.name].append(
                PendingHolidayMakeupTask(
                    nurse_idx=nurse_idx_map[nurse.name],
                    nurse_name=nurse.name,
                    reserved_column=column,
                    original_slot=None,
                    original_text=record.get('original_text', ''),
                    adjust_type=None,
                    source='prev_month',
                    column_hint=record.get('column_hint'),
                )
            )

    fixed_results.sort(key=lambda r: (_slot_sort_key(r.slot), r.column_index, r.nurse.name))
    state.seed_results.extend(fixed_results)
    return state


def schedule_holiday_shifts(
    nurses: List['NurseInfo'],
    target_year: int,
    target_month: int,
    last_assigned_index: int = -1,
    include_next_month_first: bool = True,
    *,
    seed_state: Optional[HolidaySeedState] = None,
    unresolved_pending_label_mode: str = 'carry_next_month',
) -> Tuple[List['HolidayShiftResult'], str]:
    """
    新版 slot-based 假日排班：
    1. replay seed fixed 推進輪值指標
    2. 逐 slot 排班
    3. deferred makeup 在「輪到本人」且 slot 相容時回填
    4. 欄位完全由 per-nurse reserve/claim 控制，不再用 cycle + 1
    """
    if not nurses:
        return [], ''

    state = seed_state or build_holiday_seed_state(nurses, target_year, target_month)
    results: List['HolidayShiftResult'] = list(state.seed_results)
    occupied_slots = set(state.occupied_slots)
    next_col_by_nurse = dict(state.next_col_by_nurse)
    used_cols_by_nurse = {k: set(v) for k, v in state.used_cols_by_nurse.items()}
    pending_by_nurse = defaultdict(deque, {k: deque(v) for k, v in state.pending_by_nurse.items()})

    def reserve_column(nurse_name: str, preferred: Optional[int] = None) -> int:
        temp_state = HolidaySeedState(
            next_col_by_nurse=next_col_by_nurse,
            used_cols_by_nurse=used_cols_by_nurse,
        )
        return _reserve_column(temp_state, nurse_name, preferred)

    nurse_index_map = {n.name: idx for idx, n in enumerate(nurses)}
    current_index = (last_assigned_index + 1) % len(nurses)
    last_normal_assigned_name = ''
    used_compensations: Dict[str, List[datetime]] = {}

    # 先 replay seed fixed，推進 current_index
    for seed in sorted(state.seed_results, key=lambda r: _slot_sort_key(r.slot)):
        current_index = (nurse_index_map[seed.nurse.name] + 1) % len(nurses)

    all_slots = get_holiday_shift_slots_for_month(
        target_year,
        target_month,
        include_next_month_first,
    )

    for slot in all_slots:
        if _slot_key(slot) in occupied_slots:
            continue

        assigned = False
        scanned = 0

        while scanned < len(nurses):
            nurse = nurses[current_index]
            scanned += 1

            # A. 若此人有 pending，且這個 slot 可拿來回填，優先消化「自己的」pending
            own_queue = pending_by_nurse[nurse.name]
            if own_queue:
                task = own_queue[0]
                can_use_makeup = (
                    task.adjust_type is None or
                    (task.adjust_type == 'day_only' and slot.shift_name == '白班') or
                    (task.adjust_type == 'day_or_small_night' and slot.shift_name in {'白班', '小夜'})
                )

                if can_use_makeup:
                    skip_for_makeup = check_should_skip_holiday_shift(
                        nurse,
                        slot,
                        results,
                        target_year,
                        used_compensations,
                        check_compensation=False,  # 回填待補時不再讓補償打斷
                    )
                    if not skip_for_makeup.should_skip:
                        own_queue.popleft()
                        results.append(
                            HolidayShiftResult(
                                slot=slot,
                                nurse=nurse,
                                column_index=task.reserved_column,
                                is_makeup=True,
                                original_slot_text=task.original_text or (
                                    task.original_slot.display_text if task.original_slot else ''
                                ),
                            )
                        )
                        occupied_slots.add(_slot_key(slot))
                        current_index = (current_index + 1) % len(nurses)
                        assigned = True
                        break

            # B. 正常評估此人是否可接這個 slot
            skip = check_should_skip_holiday_shift(
                nurse,
                slot,
                results,
                target_year,
                used_compensations,
            )

            if not skip.should_skip:
                results.append(
                    HolidayShiftResult(
                        slot=slot,
                        nurse=nurse,
                        column_index=reserve_column(nurse.name),
                    )
                )
                occupied_slots.add(_slot_key(slot))
                if not slot.is_next_month:
                    last_normal_assigned_name = nurse.name
                current_index = (current_index + 1) % len(nurses)
                assigned = True
                break

            # C. 國假補償：可寫 skip 記錄，slot 仍要往下找人承接
            if skip.is_compensation:
                compensation = get_holiday_compensation_for_slot(nurse, slot, used_compensations)
                if compensation is not None:
                    if nurse.name not in used_compensations:
                        used_compensations[nurse.name] = []
                    used_compensations[nurse.name].append(compensation['date'])

                    # 若此人本來已有一筆 pending，可用同一格抵銷一筆
                    if pending_by_nurse[nurse.name]:
                        reserved_column = pending_by_nurse[nurse.name].popleft().reserved_column
                    else:
                        reserved_column = reserve_column(nurse.name)

                    results.append(
                        HolidayShiftResult(
                            slot=slot,
                            nurse=nurse,
                            column_index=reserved_column,
                            is_skipped=True,
                            is_holiday_compensation=True,
                            compensation_date=compensation['date'],
                            compensation_shift_type=compensation['shift_type'],
                            skip_reason=skip.reason,
                        )
                    )

                current_index = (current_index + 1) % len(nurses)
                continue

            # D. 需要延後補班：現在就 reserve 欄位，再 enqueue
            if skip.need_find_next:
                pending_by_nurse[nurse.name].append(
                    PendingHolidayMakeupTask(
                        nurse_idx=current_index,
                        nurse_name=nurse.name,
                        reserved_column=reserve_column(nurse.name),
                        original_slot=slot,
                        original_text=slot.display_text,
                        adjust_type=skip.adjust_type,
                        source='current',
                    )
                )
                current_index = (current_index + 1) % len(nurses)
                continue

            # E. 硬性跳過但不用補：寫 skip 記錄，slot 往下找人
            results.append(
                HolidayShiftResult(
                    slot=slot,
                    nurse=nurse,
                    column_index=reserve_column(nurse.name),
                    is_skipped=True,
                    skip_reason=skip.reason,
                )
            )
            current_index = (current_index + 1) % len(nurses)

        if not assigned:
            # 建議先不要 silent fallback，把差異交給 diff tool 暴露。
            # 若你要保證一定補滿，可再加 policy: force_assign_mode。
            pass

    # F. 本月仍未解掉的 pending -> 轉成下月補 placeholder
    next_month = 1 if target_month == 12 else target_month + 1
    for nurse in nurses:
        while pending_by_nurse[nurse.name]:
            task = pending_by_nurse[nurse.name].popleft()
            if unresolved_pending_label_mode == 'carry_next_month':
                text = f'{next_month}月補'
            else:
                text = task.original_text or f'{next_month}月補'

            results.append(
                HolidayShiftResult(
                    slot=_placeholder_slot(target_year, target_month),
                    nurse=nurse,
                    column_index=task.reserved_column,
                    display_override=text,
                    is_makeup=True,
                    original_slot_text=task.original_text,
                )
            )

    # G. 標記本月最後一個「正常輪序」班次
    normal_results = [
        r for r in results
        if not r.is_makeup
        and not r.is_skipped
        and not r.is_holiday_compensation
        and not r.slot.is_next_month
    ]
    if normal_results:
        normal_results[-1].is_last_normal = True
        last_normal_assigned_name = normal_results[-1].nurse.name

    # H. 固定排序，避免輸出不穩定
    results.sort(key=lambda r: (r.nurse.name, r.column_index, _slot_sort_key(r.slot)))
    return results, last_normal_assigned_name
