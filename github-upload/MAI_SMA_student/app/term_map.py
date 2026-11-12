# -*- coding: utf-8 -*-
"""Terminology Map / 설비 용어 대응표 — 한국어 질문을 코퍼스 어휘로 옮긴다.

정비기록 본문과 검색 색인은 영어다. 한국어 질문을 번역할 때 모델이 제
나름의 영어 표현을 쓰면(예: "발전기" -> "power generator"), 코퍼스가 쓰는
표현("GENERATOR ENGINE(DIESEL)")과 어긋나 BM25 가 엉뚱한 기록을 집는다.

그래서 번역 단계에 용어 대응을 함께 넣는다. 오른쪽 영문은 모두 이 데이터의
MACH_DESC / EQUIP_DESC 에 실재하는 값이며, 지어낸 표현이 없다. 질문에 나온
말에 해당하는 줄만 골라 넣으므로 프롬프트가 길어지지 않는다.

대응표는 정비 라인 수 상위 설비(전체의 92.6%)를 중심으로 구성했다.
"""

# (한국어 표현들, 코퍼스의 영문 표기)
# 한국어는 현장에서 쓰는 여러 말을 함께 적는다. 앞의 것이 대표형이다.
TERMS = [
    (['주기관', '메인엔진', '메인 엔진', '주기', 'M/E', '엠이',
      'main engine', 'main diesel engine'], 'MAIN ENGINE(DIESEL)'),
    (['발전기', '발전기엔진', '디젤발전기', 'D/G', '디지',
      'generator engine', 'diesel generator', 'auxiliary engine'],
     'GENERATOR ENGINE(DIESEL)'),
    (['축발전기', '샤프트제너레이터', 'S/G'], 'SHAFT GENERATOR'),
    (['비상발전기', '비상 발전기', '이머전시 발전기'], "GENERATOR FOR EM`CY"),
    (['보조보일러', '보조 보일러', '보일러', '아우질러리보일러',
      'auxiliary boiler', 'aux boiler', 'boiler furnace'], 'AUX. BOILER'),
    (['배기가스 이코노마이저', '이코노마이저', '절탄기'], 'EXH. GAS ECONOMIZER'),
    (['계선윈치', '무어링윈치', '무어링 윈치', '계류윈치', '윈치',
      'mooring winch'], 'MOORING WINCH'),
    (['양묘기', '윈드라스', '윈들러스'], 'WINDLASS'),
    (['캡스턴'], 'CAPSTAN'),
    (['조타기', '스티어링기어', '스티어링 기어'], 'STEERING GEAR'),
    (['구명정', '구명정 진수장치', '데빗윈치', '라이프보트',
      'life boat', 'lifeboat', 'launching appliance', 'davit'],
     'LIFE BOAT/ENGINE/DAVIT WINCH'),
    (['구명뗏목', '구명벌', '라이프래프트'], 'LIFE RAFT'),
    (['파일럿 사다리', '도선사 사다리', '파일럿래더'], 'PILOT LADDER/WINCH/MOTOR'),
    (['현문사다리', '갱웨이', '어코모데이션 래더'], 'ACC. LADDER WINCH & MOTOR'),
    # 시동공기 압축기와 main air compressor 는 같은 기계다. 기록은 뒤의
    # 이름으로 적혀 있어, 앞의 이름으로 물으면 닿지 못한다.
    (['주공기압축기', '주 공기압축기', '시동공기압축기', '메인 에어 컴프레서',
      'starting air compressor', 'main air compressor', 'starting air'],
     'AIR COMPRESSOR FOR MAIN'),
    (['제어공기압축기', '서비스 공기압축기', '컨트롤 에어 컴프레서',
      'control air compressor', 'service air compressor'],
     'AIR COMPRESSOR FOR CONTROL/SERVICE'),
    (['고압압축기', 'HD 압축기', '하이듀티 압축기'], 'HIGH DUTY COMPRESSOR'),
    (['저압압축기', 'LD 압축기', '로우듀티 압축기'], 'LOW DUTY COMPRESSOR'),
    (['제어공기 건조기', '에어드라이어', '공기건조기'], 'CONTROL AIR DRYER'),
    (['주공기탱크', '시동공기탱크', '에어리시버'], 'AIR RESERVOIR FOR MAIN'),
    (['주윤활유청정기', '메인 LO 청정기', '주기 LO 청정기'], 'MAIN L.O PURIFIER'),
    (['발전기 윤활유청정기', '발전기 LO 청정기', 'GE LO 청정기'], 'G/E L.O PURIFIER'),
    (['경유청정기', '연료유청정기', 'LFO 청정기', '청정기', '퓨리파이어'],
     'L.F.O PURIFIER'),
    (['윤활유 자동여과기', '자동필터', 'LO 오토필터'],
     'AUTO FILTER FOR M/E L.O DISCHARGE'),
    (['윤활유 분석', '오일분석', 'LO 샘플분석'], 'LUB. OIL SAMPLE ANALYSIS'),
    (['중앙냉각 청수냉각기', '중앙냉각기', '센트럴쿨러', '청수냉각기',
      'central cooler', 'central fresh water cooler'],
     'COOLER FOR CENTRAL F.W'),
    (['보조 중앙냉각기', '보조 청수냉각기'], 'COOLER FOR AUX. CENTRAL F.W'),
    (['공기냉각기', '에어쿨러', '소기냉각기'], 'COOLER FOR M/E AIR COOLING F.W'),
    (['조수기', '청수생성기', '프레시워터제너레이터'], 'FRESH WATER GENERATOR'),
    (['청수공급장치', '청수공급유닛'], 'F.W SUPPLY UNIT SYSTEM'),
    (['해수여과기', '선저해수여과기', '시체스트', '해수흡입구',
      'sea chest', 'sea water strainer'], 'PIPE & SEA CHEST'),
    (['불활성가스장치', '이너트가스', 'IGS'], 'INERT GAS SYSTEM'),
    (['질소발생기', 'N2 발생기', '질소장치'], 'N2 GENERATOR'),
    (['재액화장치', '재액화설비'], 'RELIQUEFACTION PLANT'),
    (['평형수처리장치', '밸러스트수처리', 'BWMS', '평형수'],
     'BALLAST WATER MANAGEMENT SYSTEM'),
    (['평형수탱크', '밸러스트탱크'], 'TANK FOR BALLAST'),
    (['오수처리장치', '분뇨처리장치', '하수처리'], 'SEWAGE TREATMENT PLANT'),
    (['소각기', '인시네레이터'], 'INCINERATOR'),
    (['기름오염방지설비', '유수분리기', '빌지분리기'], 'OIL CONTAMINATION SYSTEM'),
    (['공조기', '에어컨디셔닝', '공기조화장치'], 'AIR CONDITIONING PLANT (MAIN)'),
    (['식품냉장설비', '프로비전 냉동기', '냉장고'], 'PROV. REF. PLANT'),
    (['가스탐지장치', '가스검지기', '가스디텍터'], 'GAS DETECTION SYSTEM'),
    (['국부 미스트 소화장치', '워터미스트'],
     'LOCAL WATER MIST FIRE EXTINGUSHING SYSTEM'),
    (['CO2 소화장치', '이산화탄소 소화설비'], 'CO2 FIRE EXTINGUISH SYSTEM'),
    (['안전장치 시험', '안전장치 및 경보시험', '경보시험', '세이프티 테스트'],
     'SAFETY DEVICE & ALARM TEST'),
    (['선체구조', '선체', '헐'], 'HULL STRUCTURE'),
    (['코퍼댐'], 'COFFERDAM'),
    (['트렁크 보이드', '보이드 스페이스'], 'TRUNK VOID SPACE'),
    (['프로펠러', '추진기', '축계'], 'PROPELLER & SHAFT'),
    (['선미관 밀봉장치', '스턴튜브 실', '선미관'], 'STERN TUBE SEALING SYSTEM'),
    (['상부지지대', '탑브레이싱', '톱브레이싱'], 'M/E HYD. TOP BRACING'),
    (['화물창 크레인', '카고 컴프레서룸 크레인'], 'CARGO COMPRESSOR ROOM CRANE'),
    (['호스취급 크레인', '호스핸들링 크레인'], 'CRANE FOR HOSE HANDLING'),
    (['기관실 천장 크레인', '기관실 크레인', '오버헤드 크레인'], 'CRANE FOR E/R OVERHEAD'),
    (['식량 크레인', '프로비전 크레인'], 'CRANE FOR PROVISION'),
    (['밸브 원격제어장치', '밸브리모트', 'VRC'], 'VALVE REMOTE CONTROL SYSTEM'),
    (['배전반', '스위치보드', '기동반'], 'SWITCH BOARD & STARTER'),
    (['축전지', '배터리'], 'BATTERY'),
    (['기적', '경적', '휘슬'], 'AIR WHISTLE & PISTON HORN'),
    (['항해자료기록장치', 'VDR'], 'VOYAGE DATA RECORDER'),
    (['전자해도', 'ECDIS'], 'ECDIS UNIT'),
    (['자이로컴퍼스', '자이로', '자동조타'], 'GYRO COMPASS & AUTO PILOT SYS.'),
    (['폐쇄회로 TV', 'CCTV'], 'CCTV SYSTEM'),
    (['공용안테나', '공청설비'], 'COMMUNAL AERIAL SYSTEM'),
    (['공구', '툴'], 'TOOL'),
    (['밸브류', '밸브'], 'VALVES'),
]

# 장비(EQUIP_DESC) 수준의 부품 용어. 기기명만으로는 검색이 넓어질 때 쓴다.
PARTS = [
    (['과급기', '터보차저', '터보챠저', 'T/C',
      'turbocharger', 'turbo charger'], 'TURBO CHARGER'),
    (['크랭크케이스', '크랭크실', '크랭크 챔버',
      'crankcase', 'crank case'], 'CRANK CHAMBER'),
    (['연료분사밸브', '인젝터', '연료밸브',
      'fuel injection valve', 'injector'], 'FUEL OIL V/V'),
    (['배기밸브', '배기변', '익조스트 밸브'], 'EXH VALVE & VALVE GEAR'),
    (['실린더라이너', '라이너', '냉각재킷'], 'LINER, COOLING JACKET & CYL. FRAME'),
    (['실린더커버', '실린더헤드'], 'CYLINDER COVER'),
    (['피스톤', '피스톤링'], 'PISTON'),
    (['냉각기', '쿨러', '열교환기'], 'COOLER'),
    (['볼', '보울', '분리통'], 'BOWL/VER,HOR SHAFT/BODY'),
    (['전장품', '전기부품'], 'ELECTRIC PART & MISCELLANY'),
    (['보일러 부속', '보일러 액세서리'], 'ACCESSORY FOR BOILER'),
    (['시체스트', '해수함', 'sea chest strainer'], 'SEA CHEST'),
    (['데빗윈치', '진수장치 윈치'], 'DAVIT WINCH FOR LIFE BOAT'),
    (['윈치 본체', '기계부'], 'WINCH (WITH MECHANICAL PART)'),
    (['앵커', '닻'], 'ANCHOR'),
    (['앵커체인', '닻줄'], 'ANCHOR CHAIN'),
]

_ALL = TERMS + PARTS


def hints(q, limit=8):
    """질문에 나온 말에 해당하는 대응만 골라 돌려준다.

    한국어와 영어를 모두 본다. 영어 질문은 번역 단계를 거치지 않으므로
    대응표가 걸리지 않으면 기록의 표기에 닿을 길이 없다.

    전부 넣으면 프롬프트가 수천 토큰 늘어난다. 질문에 실제로 등장한 것만
    넣으면 보통 한두 줄이고, 그 한두 줄이 검색 결과를 가른다.
    """
    s = (q or '').lower().replace(' ', '')
    out = []
    for kos, en in _ALL:
        # 가장 긴 표현부터 본다. '시동공기압축기' 가 '압축기' 보다 먼저 걸려야
        # 무엇 때문에 이 대응이 붙었는지 드러난다.
        for k in sorted(kos, key=len, reverse=True):
            if k.lower().replace(' ', '') in s:
                out.append((k, en))      # 대표형이 아니라 실제로 걸린 말
                break
        if len(out) >= limit:
            break
    return out


def hint_block(q, limit=8):
    """번역 프롬프트에 붙일 용어 대응 문단. 해당 없으면 빈 문자열."""
    h = hints(q, limit)
    if not h:
        return ''
    lines = '\n'.join(f'  {ko} = {en}' for ko, en in h)
    return ('\nUse these exact equipment names as they appear in the records:\n'
            + lines + '\n')


def vocabulary():
    """대응표가 아는 영문 표기 전체. 검색어 보강에 쓴다."""
    return [en for _, en in _ALL]
