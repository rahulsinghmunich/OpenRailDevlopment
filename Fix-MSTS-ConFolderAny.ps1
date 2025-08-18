# Fix-MSTS-ConFolderAny.ps1 (v3.4.4 – PS5+ safe sorts)
# Purpose: Repair .con files by resolving missing EngineData/WagonData references using
#          (1) same-folder token match (folder tokens excluded),
#          (2) global-from-cache/index match (excluding _DEFAULTS),
#          (3) _DEFAULTS fallback.
# Includes strict role locking, optional strict class/type, persistent logging.
#
# v3.4 changes:
#   - Engine/Wagon class & role detection now considers both name + folder (WAP/WAG/WDM/WDG/WAM/WCG + EMU/MEMU/DEMU).
#   - AI-HORN no longer hard-ignored if a default exists in _DEFAULTS; it will be resolved via defaults.
#   - Richer logging: shows needed class/role; top candidate scores (with -DebugScores); and replacement source (Local/Global/Defaults).
#
# UPDATE (v3.4.2):
#   - Live console output via Write-Progress; simple "CON:" summary lines when -LogChanges is used.
#   - _logs\FixMSTS_Summary.log is created (header "===== SUMMARY =====") when -LogChanges is passed.
#
# PATCH (v3.4.4):
#   - Removed unused -MaxFuzzy parameter.
# PATCH (v3.4.3):
#   - Replaced all "negated score" sorts with: Sort-Object -Property { (Score-Candidate ...) } -Descending
#     (avoids op_Subtraction on arrays in PS 7.x/5.x).
#
# Tested on: PowerShell 5.1 & 7.5.2

[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)] [string]$ConsistsPath,
    [Parameter(Mandatory=$true)] [string]$TrainsetDir,
    [Parameter()] [switch]$DryRun,
    [switch]$UseCache,
    [switch]$FastIndex,           # scan immediate subfolders only (no deep recurse)
    [switch]$StrictEngineClass,   # prefer same loco class (WAP/WAG/WDG/WDM)
    [switch]$StrictWagonType,     # prefer same passenger coach class (1A/2A/3A/SL/GS/CC/EOG/PC)
    [int]$MinLocalScore = 40,     # threshold for "near match" within SAME folder
    [int]$MinCacheScore = 36,     # threshold for "near match" in GLOBAL cache/index (excl. _DEFAULTS)
    [switch]$DebugScores,         # log top candidate scores
    [Parameter()] [Alias('ConciseLog','Changes','LogChange')] [switch]$LogChanges # simple "CON:" summary + console echo
)
# === BEGIN: Tokenization & Matching helpers (non-breaking additions) ===

function Normalize-ShapeName {
    param([string]$s)
    if (-not $s) { return "" }
    $n = $s.ToLowerInvariant()
    $n = $n -replace '^(generic|default|brw|vg|bsam|jjjpro|maxbcna|bti|bgpro|vsk|rws|trg|sbi|sr|nr|er)[\-\s_]+' , ''
    $n = $n -replace '[-_]+' , ' '
    $n = $n -replace '\s+'   , ' '
    $n = $n -replace '\b\d{4,6}\b', ''
    return $n.Trim()
}

$script:SeriesAlias = @{
    'wag9h'='wag9'; 'wag9i'='wag9';
    'wdg4d'='wdg4'; 'wdm3a'='wdm3'
}

function Parse-EngineTokens {
    param([string]$name)
    $n = Normalize-ShapeName $name
    $family = $null
    if ($n -match '\b(memu|demu|emu)\b') { $family = $matches[1] }
    $class = $null; $series = $null; $classSeries = $null
    if ($n -match '\b(wap|wag|wdg|wdm|wam|wcm|wcg)\s*(\d+[a-z]?)\b') {
        $class  = $matches[1]
        $series = $matches[2]
        $classSeries = "$class$series"
        if ($script:SeriesAlias.ContainsKey($classSeries)) {
            $classSeries = $script:SeriesAlias[$classSeries]
            if ($classSeries -match '^([a-z]+)(\d+[a-z]?)$') {
                $class  = $matches[1]; $series = $matches[2]
            }
        }
    }
    [pscustomobject]@{
        Family      = $family
        Class       = $class
        Series      = $series
        ClassSeries = $classSeries
    }
}

function Parse-WagonTokens {
    param([string]$name)
    $n = Normalize-ShapeName $name
    $hasIcf = $n -match '\b(icf)\b'
    $hasUtk = $n -match '\b(utk)\b'
    $stock  = if ($hasIcf -and $hasUtk) { 'icf_utk' }
              elseif ($n -match '\b(lhb)\b') { 'lhb' }
              elseif ($hasIcf) { 'icf' } else { $null }

    $coach = $null
    foreach ($pat in '1a','2a','3a','sl','gs','cc','eog','slr','pc') {
        if ($n -match "\b$pat\b") { $coach = $pat; break }
    }
    if (-not $coach) {
        if     ($n -match '\b(generator|gen)\b') { $coach = 'eog' }
        elseif ($n -match '\b(brake\s*van)\b')   { $coach = 'slr' }
        elseif ($n -match '\b(parcel|vph|vpu)\b'){ $coach = 'parcel' }
    }

    $freight = $null
    if     ($n -match '\b(blc|bll|brn)\b')         { $freight = 'container' }
    elseif ($n -match '\b(bcna|bcnhl|bcn|boxn(hl|hs)?)\b') { $freight = 'covered' }

    $containerVendor = if ($n -match '\b(concor)\b') { 'concor' } else { $null }
    $isCaboose       = ($n -match '\b(caboose|bvzi)\b')

    $setHint = $null
    if ($n -match '\b(rajdhani|duronto|humsafar|tejas|garib\s*rath|shatabdi)\b') { $setHint = $matches[1] }

    [pscustomobject]@{
        Stock           = $stock
        Coach           = $coach
        Freight         = $freight
        ContainerVendor = $containerVendor
        Caboose         = $isCaboose
        SetHint         = $setHint
    }
}

function Is-PseudoToken {
    param([string]$name)
    $n = Normalize-ShapeName $name
    return [bool]($n -match '\b(ai\s*horn|ai\-?horn|horn|sound|siren)\b')
}
# --- Fast-path helpers (exact + 2-token overlap) ---
function Get-NormKey([string]$folder,[string]$name){
    return (Normalize-Name ($folder + ' ' + $name))
}
function Get-TrimmedTokens([string]$folder,[string]$name){
    $norm = (Normalize-Name ($folder + ' ' + $name))
    if([string]::IsNullOrWhiteSpace($norm)){ return @() }
    $tokens = @($norm -split '\s+' | Where-Object { $_ -and $_.Length -ge 2 })
    $stop = @('coach','coaches','wagon','wagons','train','trains','indian','railway','railways','pack','default','generic')
    return @($tokens | Where-Object { $stop -notcontains $_ } | Select-Object -Unique)
}
function TwoTokenOverlap([string[]]$need,[string[]]$have){
    if(-not $need -or -not $have){ return $false }
    $set = New-Object 'System.Collections.Generic.HashSet[string]'
    foreach($t in $have){ if($t){ [void]$set.Add($t) } }
    $hit = 0
    foreach($t in $need){ if($t -and $set.Contains($t)){ $hit++ } if($hit -ge 2){ return $true } }
    return $false
}
function Try-FastPath([string]$kind,[string]$shape,[string]$folder,[object[]]$local,[object[]]$global){
    $needKey    = Get-NormKey $folder $shape
    $needTokens = Get-TrimmedTokens $folder $shape

    # 1) Exact in same folder
    $exLocal = @($local | Where-Object { (Get-NormKey $_.Folder (if ($_.Shape) { $_.Shape } else { $_.Name })) -eq $needKey })
    if($exLocal.Count -gt 0){
        $best = $exLocal[0]
        Write-Log 'INFO' ("FastPath hit: LocalExact for '{0}' in '{1}' -> {2}/{3}" -f $shape,$folder,(if ($best.Shape) { $best.Shape } else { $best.Name }),$best.Folder)
        return $best
    }

    # 2) Exact in global
    $exGlob = @($global | Where-Object { (Get-NormKey $_.Folder (if ($_.Shape) { $_.Shape } else { $_.Name })) -eq $needKey })
    if($exGlob.Count -gt 0){
        $best = $exGlob[0]
        Write-Log 'INFO' ("FastPath hit: GlobalExact for '{0}' in '{1}' -> {2}/{3}" -f $shape,$folder,(if ($best.Shape) { $best.Shape } else { $best.Name }),$best.Folder)
        return $best
    }

    # 3) 2-token overlap in same folder
    $twoLocal = @($local | Where-Object { TwoTokenOverlap $needTokens (Get-TrimmedTokens $_.Folder (if ($_.Shape) { $_.Shape } else { $_.Name })) })
    if($twoLocal.Count -gt 0){
        $best = ($twoLocal | Sort-Object { - [double](Score-Candidate $shape $folder $_) })[0]
        Write-Log 'INFO' ("FastPath hit: Local2Tok for '{0}' in '{1}' -> {2}/{3}" -f $shape,$folder,(if ($best.Shape) { $best.Shape } else { $best.Name }),$best.Folder)
        return $best
    }

    # 4) 2-token overlap in global
    $twoGlob = @($global | Where-Object { TwoTokenOverlap $needTokens (Get-TrimmedTokens $_.Folder (if ($_.Shape) { $_.Shape } else { $_.Name })) })
    if($twoGlob.Count -gt 0){
        $best = ($twoGlob | Sort-Object { - [double](Score-Candidate $shape $folder $_) })[0]
        Write-Log 'INFO' ("FastPath hit: Global2Tok for '{0}' in '{1}' -> {2}/{3}" -f $shape,$folder,(if ($best.Shape) { $best.Shape } else { $best.Name }),$best.Folder)
        return $best
    }

    return $null
}

# === END: Tokenization & Matching helpers ===


Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# -------------------------------------------------------------------------------------
# Paths, cache, logging
# -------------------------------------------------------------------------------------
$ScriptDir = Split-Path -Parent $(if ($PSCommandPath) { $PSCommandPath } else { $MyInvocation.MyCommand.Path })
$LogsDir   = Join-Path $ScriptDir '_logs'
if(-not (Test-Path -LiteralPath $LogsDir)){
    New-Item -ItemType Directory -Path $LogsDir -Force | Out-Null
}
$LogPath       = Join-Path $LogsDir 'FixMSTS_Run.log'
$SummaryPath   = Join-Path $LogsDir 'FixMSTS_Summary.log'
$SummaryHeader = "===== SUMMARY ====="

$DefaultsFolderName = '_DEFAULTS'
$DefaultsDir        = Join-Path $TrainsetDir $DefaultsFolderName
$CachePath          = Join-Path $TrainsetDir '_FixMSTS_IndexCache.json'

# initialize summary file when requested
if ($LogChanges) {
    Set-Content -LiteralPath $SummaryPath -Value ($SummaryHeader + "`n")
}

function Write-Log([string]$level,[string]$msg){
    $ts = (Get-Date).ToString('o')
    $line = "[$ts] [$level] $msg"
    if (-not $LogChanges) {
        Add-Content -LiteralPath $LogPath -Value $line
    } else {
        if ($level -eq 'ERR' -or $msg -like '[REPLACE]*' -or $msg -like '[OK]*' -or $msg -like '[NOMATCH]*') {
            Add-Content -LiteralPath $LogPath -Value $line
        }
    }
    if ($level -eq 'ERR') { Write-Host "[ERROR] $msg" -ForegroundColor Red }
}

# simple summary helpers (echo + file)
function Write-SummaryLine {
    param([Parameter(Mandatory)][string]$Text)
    if ($LogChanges) {
        Add-Content -LiteralPath $SummaryPath -Value $Text
        Write-Host $Text
    }
}

# unified replacement logger for detailed file + simple summary

# ===================== TOKEN MAP HELPER (memoized; no logging changes) =====================
# Builds token maps from _FixMSTS_IndexCache.json and suggests candidates by tokens+scores.
# Does not write logs; caller should use existing Write-ReplaceLog / Write-Log.

$script:TM_LastCachePath   = $null
$script:TM_LastCacheStamp  = $null
$script:TM_CachedTokenMaps = $null

function TM-Normalize([string]$s){
    if([string]::IsNullOrEmpty($s)){ return '' }
    $t = $s.ToLowerInvariant() -replace '[^a-z0-9]+',' ' -replace '\s+',' '
    return $t.Trim()
}
function TM-Tokens([string]$s){
    if([string]::IsNullOrEmpty($s)){ return @() }
    return (TM-Normalize $s).Split(' ', [System.StringSplitOptions]::RemoveEmptyEntries) | Select-Object -Unique
}
function TM-FolderType([string]$s){
    $n = TM-Normalize $s
    if($n -match '\blhb\b'){ return 'lhb' }
    if($n -match '\bicf\b'){ return 'icf' }
    if($n -match '\b(mem u|emu)\b' -or $n -match '\bmemu\b' -or $n -match '\bemu\b'){ return 'emu' }
    if($n -match '\b(wap|wag|wdm|wdg|wam|wcg)\b'){ return 'loco' }
    if($n -match '\b(freight|goods|brn|flat|container|concor|bccw|bcfc|bcn)\b'){ return 'freight' }
    if($n -match '\b(parcel|slr|eog)\b'){ return 'parcel' }
    if($n -match '\bcaboose\b'){ return 'caboose' }
    return 'other'
}
function TM-Jaccard([string[]]$A,[string[]]$B){
    if(-not $A -or -not $B){ return 0.0 }
    $sa = [System.Collections.Generic.HashSet[string]]::new($A)
    $sb = [System.Collections.Generic.HashSet[string]]::new($B)
    $inter = [System.Collections.Generic.HashSet[string]]::new($sa); $null = $inter.IntersectWith($sb)
    $union = [System.Collections.Generic.HashSet[string]]::new($sa); $null = $union.UnionWith($sb)
    if($union.Count -eq 0){ return 0.0 }
    return [math]::Round($inter.Count / [double]$union.Count, 4)
}

function TM-GetTokenMaps{
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$CachePath)
    $fi = Get-Item -LiteralPath $CachePath -ErrorAction Stop
    $stamp = $fi.LastWriteTimeUtc.Ticks
    if($script:TM_CachedTokenMaps -and $CachePath -eq $script:TM_LastCachePath -and $stamp -eq $script:TM_LastCacheStamp){
        return $script:TM_CachedTokenMaps
    }
    $json = Get-Content -LiteralPath $CachePath -Raw -Encoding UTF8 | ConvertFrom-Json
    $eng = @($json.Engines); $wag = @($json.Wagons)

    function _BuildMap([object[]]$entries){
        $tokenMap = [System.Collections.Generic.Dictionary[string, System.Collections.Generic.List[int]]]::new([System.StringComparer]::OrdinalIgnoreCase)
        for($i=0; $i -lt $entries.Count; $i++){
            $e = $entries[$i]
            $all = "{0} {1} {2}" -f ($e.Norm), ($e.Name), ($e.Folder)
            foreach($t in (TM-Tokens $all)){
                if(-not $tokenMap.ContainsKey($t)){ $tokenMap[$t] = [System.Collections.Generic.List[int]]::new() }
                $tokenMap[$t].Add($i)
            }
        }
        return $tokenMap
    }

    $maps = [ordered]@{
        Engine = [ordered]@{ Entries = $eng; TokenMap = (_BuildMap $eng) }
        Wagon  = [ordered]@{ Entries = $wag; TokenMap = (_BuildMap $wag) }
    }
    $script:TM_LastCachePath   = $CachePath
    $script:TM_LastCacheStamp  = $stamp
    $script:TM_CachedTokenMaps = $maps
    return $maps
}

function TM-ScoreCandidate{
    param(
        [Parameter(Mandatory)][string]$OldShape,
        [Parameter(Mandatory)][string]$OldFolder,
        [Parameter(Mandatory)][hashtable]$Cand
    )
    $toksT = TM-Tokens ("$OldShape $OldFolder")
    $toksC = TM-Tokens ("$($Cand.Norm) $($Cand.Name) $($Cand.Folder)")

    $base  = TM-Jaccard $toksT $toksC
    $tFT = TM-FolderType $OldFolder
    $cFT = TM-FolderType $Cand.Folder
    $ftBonus = if(($tFT -ne 'other') -and ($tFT -eq $cFT)){ 0.15 } else { 0.0 }
    $sameFolderBonus = if((TM-Normalize $Cand.Folder) -eq (TM-Normalize $OldFolder)){ 0.20 } else { 0.0 }

    $classTokens = @('wap','wag','wdm','wdg','lhb','icf','memu','emu','eog','slr','1a','2a','3a','cc','gs')
    $contTokens  = @('concor','container','bccw','bcfc','bcn','bccn','bccr','bcacbm','flat','blc','blrn','brn')
    $clsBonus = [math]::Min(0.10, 0.02 * (@($toksT | Where-Object { $_ -in $classTokens -and $_ -in $toksC }).Count))
    $contBonus = 0.0
    if(($tFT -eq 'freight') -or (@($toksT | Where-Object { $_ -in $contTokens }).Count -gt 0)){
        if($cFT -eq 'freight'){ $contBonus += 0.10 }
        $contBonus += [math]::Min(0.10, 0.02 * (@($toksT | Where-Object { $_ -in $contTokens -and $_ -in $toksC }).Count))
    }
    return [math]::Round($base + $ftBonus + $sameFolderBonus + $clsBonus + $contBonus, 4)
}

function TM-Suggest{
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][hashtable]$TokenMaps,
        [Parameter(Mandatory)][ValidateSet('EngineData','WagonData')][string]$Section,
        [Parameter(Mandatory)][string]$OldShape,
        [Parameter(Mandatory)][string]$OldFolder,
        [int]$Top = 3,
        [double]$MinScore = 0.0
    )
    $kind = if($Section -eq 'EngineData'){ 'Engine' } else { 'Wagon' }
    $entries  = @($TokenMaps[$kind].Entries)
    $tokenMap = $TokenMaps[$kind].TokenMap

    $toks = TM-Tokens ("$OldShape $OldFolder")
    $pool = [System.Collections.Generic.HashSet[int]]::new()
    foreach($t in $toks){
        if($tokenMap.ContainsKey($t)){
            foreach($idx in $tokenMap[$t]){ $null = $pool.Add($idx) }
        }
    }
    if($pool.Count -eq 0){
        for($i=0; $i -lt $entries.Count; $i++){ $null = $pool.Add($i) }
    }

    $scored = foreach($i in $pool){
        $e = $entries[$i]
        $cand = @{ Name=$e.Name; Folder=$e.Folder; Norm=$e.Norm; Path=$e.Path }
        $score = TM-ScoreCandidate -OldShape $OldShape -OldFolder $OldFolder -Cand $cand
        if($score -ge $MinScore){
            [pscustomobject]@{
                Score      = $score
                Cand       = $cand
                Kind       = $kind
                PoolCount  = $pool.Count
            }
        }
    }
    $scored | Sort-Object Score -Descending | Select-Object -First $Top
}
# =================== END TOKEN MAP HELPER ===================
function Write-ReplaceLog {
    param(
        [Parameter(Mandatory)] [string]$ConName,
        [Parameter(Mandatory)] [ValidateSet('EngineData','WagonData')] [string]$Section,

        # accept anything; coerce to string(s) inside
        [Parameter(Mandatory)] [AllowNull()] [object]$OrigId,
        [AllowNull()] [object]$OrigFolder = '',
        [Parameter(Mandatory)] [AllowNull()] [object]$NewId,
        [AllowNull()] [object]$NewFolder = '',

        # IMPORTANT: make Source flexible; no ValidateSet here (we’ll sanitize)
        [AllowNull()] [object]$Source = $null,

        [int]$Score = -1,
        [string]$ClassInfo = ''
    )

    # --- normalize inputs to single strings (pick first if arrays) ---
    $oi = if ($OrigId     -is [System.Array]) { $OrigId[0]     } else { $OrigId }
    $of = if ($OrigFolder -is [System.Array]) { $OrigFolder[0] } else { $OrigFolder }
    $ni = if ($NewId      -is [System.Array]) { $NewId[0]      } else { $NewId }
    $nf = if ($NewFolder  -is [System.Array]) { $NewFolder[0]  } else { $NewFolder }
    $srcRaw = if ($Source -is [System.Array]) { $Source[0] } else { $Source }

    $oi = [string]$oi; $of = [string]$of; $ni = [string]$ni; $nf = [string]$nf
    $srcStr = [string]$srcRaw

    # sanitize/normalize Source to known labels
    switch -Regex ($srcStr) {
        '^(?i)local$'    { $srcNorm = 'Local';    break }
        '^(?i)global$'   { $srcNorm = 'Global';   break }
        '^(?i)defaults?$'{ $srcNorm = 'Defaults'; break }
        '^(?i)exact$'    { $srcNorm = 'Exact';    break }
        default          { $srcNorm = 'Unknown' }
    }

    # detailed log
    $from = if ($of) { "$oi $of" } else { $oi }
    $to   = if ($nf) { "$ni $nf" } else { $ni }

    $meta = @()
    if ($srcNorm)  { $meta += "source=$srcNorm" }
    if ($Score -ge 0) { $meta += "score=$Score" }
    if ($ClassInfo) { $meta += "class=$ClassInfo" }
    $metaStr = if ($meta.Count) { " [" + ($meta -join ', ') + "]" } else { "" }
    Write-Log 'INFO' ("[REPLACE] CON: {0} {1} {2}  ->  {3}{4}" -f $ConName,$Section,$from,$to,$metaStr)

    # simple summary
    $reason = switch ($srcNorm) {
        'Local'    { 'same-folder type' }
        'Global'   { 'type token-first' }
        'Defaults' { 'defaults' }
        'Exact'    { 'exact' }
        default    { 'match' }
    }
    Write-SummaryLine ("CON: {0} {1} ({2} {3}) -> ({4} {5}) [{6}]" -f $ConName,$Section,$oi,$of,$ni,$nf,$reason)
}



# -------------------------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------------------------
function Normalize-Name([string]$name) {
    if ([string]::IsNullOrWhiteSpace($name)) { return '' }

    # start with trimmed text
    $n = $name.Trim()

    # strip common vendor prefixes only when they appear at the start (case-insensitive)
    $n = $n -replace '^(?i:(?:Generic_|DEFAULT_|BRW_|VG_|SJ_|BSAM_|JJJPro_|MAXBCNA_|BTI_|BGPro_|VSK_|RWS_|ASM_|HR_|MLW_|TNCOACHES_|KYN_|RTW_|MSA_|INDIAN_|INDIAN2_))', ''

    # normalize separators to single spaces
    $n = ($n -replace '[^A-Za-z0-9]+', ' ').Trim()

    # lowercase for stable matching
    return $n.ToLowerInvariant()
}
   

function Safe-Array($x){ if($null -eq $x){ return @() }; if($x -is [System.Array]){ return $x }; return @($x) }
function New-Record([string]$kind,[string]$name,[string]$folder,[string]$path){
    $folderU = $folder.Trim('"'); $nameU = $name.Trim('"')
    [pscustomobject]@{
        Kind   = $kind; Name = $nameU; Folder = $folderU; Path = $path
        Key    = ($folderU.ToLowerInvariant() + '|' + $nameU.ToLowerInvariant())
        Norm   = Normalize-Name $nameU
    }
}

# pseudo shapes
$IgnoreShapePatterns = @('^\s*HORN\b','^\s*SOUND\b', '^\s*AI[-\s_]?HORN\b') | ForEach-Object { [regex]::new($_,'IgnoreCase') }
function Has-DefaultFor([string]$shape,[string]$kind){
    if(-not $Index.ByFolder.ContainsKey($DefaultsFolderName)){ return $false }
    $norm = Normalize-Name $shape
    $pool = @($Index.ByFolder[$DefaultsFolderName] | Where-Object { $_.Kind -eq $kind })
    $hit  = $pool | Where-Object { $_.Name -eq $shape -or $_.Norm -eq $norm -or $_.Name -like "*$shape*" } | Select-Object -First 1
    return [bool]$hit
}
function Should-IgnoreShape([string]$shape,[string]$kind='Wagon'){
    foreach($re in $IgnoreShapePatterns){
        if($re.IsMatch($shape)){ if(Has-DefaultFor $shape $kind){ return $false }; return $true }
    }
    return $false
}

# engine class/role
function Get-EngineClass([string]$name,[string]$folder=''){
    $u = ($name + ' ' + $folder).ToUpperInvariant()
    if($u -match '\bWAP[\s\-_]*\d+') { return 'WAP' }
    if($u -match '\bWAG[\s\-_]*\d+') { return 'WAG' }
    if($u -match '\bWDG[\s\-_]*\d+') { return 'WDG' }
    if($u -match '\bWDM[\s\-_]*\d+') { return 'WDM' }
    if($u -match '\bWAM[\s\-_]*\d+') { return 'WAM' }
    if($u -match '\bWCG[\s\-_]*\d+') { return 'WCG' }
    if($u -match '\bEMU\b')          { return 'EMU' }
    if($u -match '\bMEMU\b')         { return 'MEMU' }
    if($u -match '\bDEMU\b')         { return 'DEMU' }
    return ''
}
function Get-EngineRole([string]$name,[string]$folder){
    $u = ($name + ' ' + $folder).ToUpperInvariant()
    $cls = Get-EngineClass $name $folder
    if($cls -in @('WAP','WDM','WAM','EMU','MEMU','DEMU')){ return 'Engine:Passenger' }
    if($cls -in @('WAG','WCG','WDG')){ return 'Engine:Freight' }
    if($u -match '\bFREIGHT\b'){ return 'Engine:Freight' }
    if($u -match '\bPASS(ENGER)?\b'){ return 'Engine:Passenger' }
    return 'Engine:Unknown'
}

# wagon role/coach type
function Get-WagonRole([string]$name,[string]$folder){
    $u = ($name + ' ' + $folder).ToUpperInvariant()
    if($u -match '\b(CABOOSE|BVZI|BVZ|BVCM|SLR|SLRD|BRAKE\s?VAN|GUARD)\b'){ return 'Wagon:Caboose' }
    if($u -match '\b(HCPV|PARCEL)\b'){ return 'Wagon:Parcel' }
    if($u -match '\b(CON_|CONCOR|BLCA|BLCB|BFNV|BLC|BFNS|BRN|BLL|CONTAINER|FLAT|UASC|MSC|MAERSK|HAPAG|TRITON|CMA|ONE|EVERGREEN|SEACO|SUD|SAFMARINE|MOL|HANJIN|GESEACO)\b'){ return 'Wagon:Container' }
    if($u -match '\b(MEMU|DEMU|EMU)\b'){ return 'Wagon:Passenger' }
    if($u -match '\b(BCNA|BOXN|BOBYN|BRN|BTFLN|BVZI|HOPPER|TANK|FLAT|GOND|NBOX|BOX|NMG)\b'){ return 'Wagon:Freight' }
    if($u -match '\b(LHB|ICF)\b'){ return 'Wagon:Passenger' }
    if($u -match '\b(1A|2A|3A|SL|GS|GEN|CC|EOG|SLR|PC|CHAIR|RAJDHANI|HUMSAFAR|DURONTO|TEJAS)\b'){ return 'Wagon:Passenger' }
    if($u -match '\bFREIGHT\b'){ return 'Wagon:Freight' }
    if($u -match '\bPASS(ENGER)?\b'){ return 'Wagon:Passenger' }
    return 'Wagon:Unknown'
}
function Get-CoachType([string]$name){
    $u = $name.ToUpperInvariant()
    if($u -match '\b1A\b|\bAC_FIRST\b'){ return '1A' }
    if($u -match '\b2A\b|\bAC_2\b'){ return '2A' }
    if($u -match '\b3A\b|\bAC_3\b|\b3_TIER\b'){ return '3A' }
    if($u -match 'SLEEPER' -or $u -match '\bSLP\b'){ return 'SL' }
    if($u -match '\bGEN\b|\bGS(_WW)?\b|\bSECONDCLASS\b'){ return 'GS' }
    if($u -match '\bCC\b|CHAIR'){ return 'CC' }
    if($u -match '\bEOG\b'){ return 'EOG' }
    if($u -match '\bPC\b'){ return 'PC' }
    return ''
}

# -------------------------------------------------------------------------------------
# Index structure & I/O
# -------------------------------------------------------------------------------------
$Index = [ordered]@{
    Engines  = @()
    Wagons   = @()
    MapExact = @{}
    ByFolder = @{}
    ByNorm   = @{}
}
function Add-To-Index([pscustomobject]$rec){
    if($rec.Kind -eq 'Engine'){ $Index.Engines += ,$rec } else { $Index.Wagons += ,$rec }
    $Index.MapExact[$rec.Key] = $rec
    if(-not $Index.ByFolder.ContainsKey($rec.Folder)){ $Index.ByFolder[$rec.Folder] = @() }
    $Index.ByFolder[$rec.Folder] += ,$rec
    if(-not $Index.ByNorm.ContainsKey($rec.Norm)){ $Index.ByNorm[$rec.Norm] = @() }
    $Index.ByNorm[$rec.Norm] += ,$rec
}
function Build-Index(){
    Write-Log 'INFO' "Indexing Trainset…"
    if($FastIndex){
        $dirs = Get-ChildItem -LiteralPath $TrainsetDir -Directory -ErrorAction SilentlyContinue
        $engs = foreach($d in $dirs){ Get-ChildItem -LiteralPath $d.FullName -Filter '*.eng' -File -ErrorAction SilentlyContinue }
        $wags = foreach($d in $dirs){ Get-ChildItem -LiteralPath $d.FullName -Filter '*.wag' -File -ErrorAction SilentlyContinue }
    }else{
        $engs = Get-ChildItem -LiteralPath $TrainsetDir -Recurse -Filter '*.eng' | Where-Object { -not $_.PSIsContainer }
        $wags = Get-ChildItem -LiteralPath $TrainsetDir -Recurse -Filter '*.wag' | Where-Object { -not $_.PSIsContainer }
    }
    foreach($f in $engs){ Add-To-Index (New-Record 'Engine' $f.BaseName $f.Directory.Name $f.FullName) }
    foreach($f in $wags){ Add-To-Index (New-Record 'Wagon'  $f.BaseName $f.Directory.Name $f.FullName) }

    if(Test-Path -LiteralPath $DefaultsDir){
        $dEng = Get-ChildItem -LiteralPath $DefaultsDir -Filter '*.eng' -File -ErrorAction SilentlyContinue
        $dWag = Get-ChildItem -LiteralPath $DefaultsDir -Filter '*.wag' -File -ErrorAction SilentlyContinue
        foreach($f in $dEng){ Add-To-Index (New-Record 'Engine' $f.BaseName $DefaultsFolderName $f.FullName) }
        foreach($f in $dWag){ Add-To-Index (New-Record 'Wagon'  $f.BaseName $DefaultsFolderName $f.FullName) }
        Write-Log 'INFO' ("Indexed defaults folder: {0}" -f $DefaultsFolderName)
    }
    Write-Log 'INFO' ("Indexed engines: {0}, wagons: {1}" -f $Index.Engines.Count,$Index.Wagons.Count)
}
function Save-Index(){
    try{
        $obj = [pscustomobject]@{ Engines = $Index.Engines; Wagons = $Index.Wagons }
        $obj | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $CachePath -Encoding UTF8
        Write-Log 'INFO' "Saved index cache to: $CachePath"
    }catch{ Write-Log 'WARN' "Failed to save cache: $($_.Exception.Message)" }
}
function Load-Index(){
    if(-not (Test-Path -LiteralPath $CachePath)){ return $false }
    try{
        $raw   = Get-Content -LiteralPath $CachePath -Raw -ErrorAction Stop
        $cache = $raw | ConvertFrom-Json -ErrorAction Stop
        $hasEng = ($cache.PSObject.Properties.Name -contains 'Engines')
        $hasWag = ($cache.PSObject.Properties.Name -contains 'Wagons')
        if(-not ($hasEng -and $hasWag)){ Write-Log 'WARN' 'Cache shape invalid; rebuilding.'; return $false }
        $engs = Safe-Array $cache.Engines
        $wags = Safe-Array $cache.Wagons
        if(($engs | Measure-Object).Count -eq 0 -and ($wags | Measure-Object).Count -eq 0){ return $false }
        foreach($r in $engs){ Add-To-Index (New-Record 'Engine' $r.Name $r.Folder $r.Path) }
        foreach($r in $wags){ Add-To-Index (New-Record 'Wagon'  $r.Name $r.Folder $r.Path) }
        Write-Log 'INFO' ("Loaded index from cache (engines: {0}, wagons: {1})." -f $Index.Engines.Count,$Index.Wagons.Count)
        return $true
    }catch{ Write-Log 'WARN' "Failed to read cache: $($_.Exception.Message)"; return $false }
}

# -------------------------------------------------------------------------------------
# Tokens & similarity
# -------------------------------------------------------------------------------------
function Expand-Tokens([string[]]$base){
    $set = New-Object 'System.Collections.Generic.HashSet[string]'
    foreach($t in $base){
        if([string]::IsNullOrWhiteSpace($t)){ continue }
        $t2 = $t.Trim().ToLowerInvariant()
        if($t2 -eq ''){ continue }
        [void]$set.Add($t2)

        if($t2 -match '^[a-z]+[0-9]+$'){
            $letters = ($t2 -replace '([a-z]+)[0-9]+', '$1')
            $digits  = ($t2 -replace '[a-z]+([0-9]+)', '$1')
            if($letters){ [void]$set.Add($letters) }
            if($digits){  [void]$set.Add($digits)  }
        } elseif($t2 -match '^[0-9]+[a-z]+$'){
            $digits  = ($t2 -replace '([0-9]+)[a-z]+', '$1')
            $letters = ($t2 -replace '[0-9]+([a-z]+)', '$1')
            if($letters){ [void]$set.Add($letters) }
            if($digits){  [void]$set.Add($digits)  }
        }

        switch -Regex ($t2) {
            '^(emu)$'   { [void]$set.Add('electric'); [void]$set.Add('passenger'); [void]$set.Add('multipleunit'); break }
            '^(memu)$'  { [void]$set.Add('electric'); [void]$set.Add('passenger'); [void]$set.Add('multipleunit'); break }
            '^(demu)$'  { [void]$set.Add('diesel');   [void]$set.Add('passenger'); [void]$set.Add('multipleunit'); break }

            '^(freight|goods|parcel|container|cargo)$' { [void]$set.Add('freight'); [void]$set.Add('goods'); [void]$set.Add('wagon'); break }

            '^boxn$'                 { [void]$set.Add('freight'); [void]$set.Add('goods'); [void]$set.Add('wagon'); break }
            '^bcna$'                 { [void]$set.Add('freight'); [void]$set.Add('goods'); [void]$set.Add('wagon'); break }
            '^bcnhl[0-9]*$'          { [void]$set.Add('freight'); [void]$set.Add('goods'); [void]$set.Add('wagon'); break }
            '^brna?$'                { [void]$set.Add('freight'); [void]$set.Add('goods'); [void]$set.Add('wagon'); break }
            '^blc[ab]$'              { [void]$set.Add('freight'); [void]$set.Add('container'); [void]$set.Add('wagon'); break }
            '^bcbfg$'                { [void]$set.Add('freight'); [void]$set.Add('wagon'); break }
            '^bccw$'                 { [void]$set.Add('freight'); [void]$set.Add('wagon'); break }
            '^bvzi?$'                { [void]$set.Add('freight'); [void]$set.Add('wagon'); [void]$set.Add('caboose'); break }
            '^tank$'                 { [void]$set.Add('freight'); [void]$set.Add('wagon'); break }
            '^hopper$'               { [void]$set.Add('freight'); [void]$set.Add('wagon'); break }
            '^coil$'                 { [void]$set.Add('freight'); [void]$set.Add('wagon'); break }
            '^flat$'                 { [void]$set.Add('freight'); [void]$set.Add('wagon'); break }
            '^sbc$'                  { [void]$set.Add('freight'); [void]$set.Add('wagon'); break }
            '^cbc$'                  { [void]$set.Add('freight'); [void]$set.Add('wagon'); break }

            '^(utk|utkr?isht)$'      { [void]$set.Add('utk'); [void]$set.Add('icf_utk'); break }
            '^lhb$'                  { [void]$set.Add('lhb'); break }
            '^icf$'                  { [void]$set.Add('icf'); break }
            '^gs$'                   { [void]$set.Add('general'); break }
            '^cc$'                   { [void]$set.Add('chair'); [void]$set.Add('ac'); break }
            '^sl$'                   { [void]$set.Add('sleeper'); break }
            '^eog$'                  { [void]$set.Add('generator'); break }
            '^slr$'                  { [void]$set.Add('brake'); [void]$set.Add('guard'); [void]$set.Add('van'); break }
            '^pc$'                   { [void]$set.Add('pantry'); break }

            '^wap$' { [void]$set.Add('electric'); [void]$set.Add('passenger'); break }
            '^wag$' { [void]$set.Add('electric'); [void]$set.Add('freight');    break }
            '^wdm$' { [void]$set.Add('diesel');   [void]$set.Add('mixed');      break }
            '^wdg$' { [void]$set.Add('diesel');   [void]$set.Add('freight');    break }
            '^wam$' { [void]$set.Add('electric'); [void]$set.Add('passenger');  break }
            '^wcg$' { [void]$set.Add('electric'); [void]$set.Add('freight');    break }

            '^(rajdhani|duronto|shatabdi)$' { [void]$set.Add($t2); break }
        }
    }
    $arr = @($set)
    for($i=0; $i -lt ($arr.Count-1); $i++){
        $bg = ($arr[$i] + '_' + $arr[$i+1])
        if($bg){ [void]$set.Add($bg) }
    }
    return ,@($set)
}

function Split-Tokens([string]$s){
    if([string]::IsNullOrWhiteSpace($s)){ return @() }
    $norm = Normalize-Name $s
    $base = @($norm -split '\s+' | Where-Object { $_.Length -gt 0 })
    return (Expand-Tokens $base)
}

function Jaccard([string]$a,[string]$b){
    $ta = New-Object 'System.Collections.Generic.HashSet[string]'
    foreach($t in (Split-Tokens $a)){ if($t){ [void]$ta.Add($t) } }
    $tb = New-Object 'System.Collections.Generic.HashSet[string]'
    foreach($t in (Split-Tokens $b)){ if($t){ [void]$tb.Add($t) } }
    if($ta.Count -eq 0 -and $tb.Count -eq 0){ return 0.0 }
    $inter = 0; foreach($t in $ta){ if($tb.Contains($t)){ $inter++ } }
    $union = ($ta.Count + $tb.Count - $inter)
    if($union -le 0){ return 0.0 }
    return [double]($inter) / [double]($union)
}

    $tb = New-Object 'System.Collections.Generic.HashSet[string]'; foreach($t in (Split-Tokens $b)){ if($t){ [void]$tb.Add($t) } }
    if($ta.Count -eq 0 -and $tb.Count -eq 0){ return 0.0 }
    $inter = 0; foreach($t in $ta){ if($tb.Contains($t)){ $inter++ } }
    $union = ($ta.Count + $tb.Count - $inter); if($union -eq 0){ return 0.0 }
    return [double]$inter / [double]$union


function Score-Candidate([string]$shape,[string]$folder,[pscustomobject]$cand){    # === Guardrails & token parsing (added) ===
    if (Is-PseudoToken $shape) { return -9999 }

    $srcEngine = Parse-EngineTokens $shape
    $srcWagon  = Parse-WagonTokens  $shape

    $candName = if ($cand.PSObject.Properties['Name']) { $cand.Name } elseif ($cand.PSObject.Properties['name']) { $cand.name } else { "$cand" }
    $candEngine = Parse-EngineTokens $candName
    $candWagon  = Parse-WagonTokens  $candName

    # Never cross EMU/MEMU/DEMU with WAP/WAG/WDG/WDM classes
    if ($srcEngine.Family -and $candEngine.Class) { return -9999 }
    if ($srcEngine.Class  -and $candEngine.Family) { return -9999 }

    # Strict engine class: same primary required
    if ($StrictEngineClass -and $srcEngine.Class) {
        if (-not $candEngine.Class -or $candEngine.Class -ne $srcEngine.Class) { return -9999 }
    }

    # Strict wagon type: enforce stock + coach/freight/vendor/caboose when present
    if ($StrictWagonType) {
        if ($srcWagon.Stock -and $candWagon.Stock -and $srcWagon.Stock -ne $candWagon.Stock) { return -9999 }
        if ($srcWagon.Coach -and $candWagon.Coach -and $srcWagon.Coach -ne $candWagon.Coach) { return -9999 }
        if ($srcWagon.Freight -and $candWagon.Freight -and $srcWagon.Freight -ne $candWagon.Freight) { return -9999 }
        if ($srcWagon.ContainerVendor -and $candWagon.ContainerVendor -and $srcWagon.ContainerVendor -ne $candWagon.ContainerVendor) { return -9999 }
        if ($srcWagon.Caboose -and -not $candWagon.Caboose) { return -9999 }
    }
    # === End guardrails ===

    $score = 0
    if($cand.Folder -eq $folder){ $score += 65 }
    $shapeTokens  = New-Object 'System.Collections.Generic.HashSet[string]'
    foreach($t in (Split-Tokens $shape)){ [void]$shapeTokens.Add($t) }
    $FolderStop = @('bgpro','bgp','asm','mfw','pack','ir','wagons','wagon','coaches','coach','freight')
    foreach($ft in (Split-Tokens $folder)){
        if($FolderStop -contains $ft){ if($shapeTokens.Contains($ft)){ $shapeTokens.Remove($ft) } }
    }
    $candTokens   = New-Object 'System.Collections.Generic.HashSet[string]'
    foreach($t in (Split-Tokens $cand.Name)){ [void]$candTokens.Add($t) }
    $shapeNormFiltered = ($shapeTokens | Sort-Object) -join ' '
    $candNorm          = Normalize-Name $cand.Name
    if($shapeNormFiltered -eq $candNorm){ $score += 40 }
    $sNorm = $shapeNormFiltered
    if([string]::IsNullOrWhiteSpace($sNorm)){ $sNorm = Normalize-Name $shape }
    if($shapeNormFiltered -eq (Normalize-Name $folder)){ $score += 8 }
    if($cand.Name -like "*$shape*" -or $cand.Norm -like "*$sNorm*"){ $score += 20 }
    $inter = 0; foreach($t in $shapeTokens){ if($candTokens.Contains($t)){ $inter++ } }
    $union = ($shapeTokens.Count + $candTokens.Count - $inter)
    if($union -gt 0){ $score += [Math]::Min([int](100 * ($inter / $union)), 30) }
        # === Token-based boosts (added) ===
    if ($srcEngine.ClassSeries -and $candEngine.ClassSeries -and $srcEngine.ClassSeries -eq $candEngine.ClassSeries) {
        $score += 40
    } elseif ($srcEngine.Class -and $candEngine.Class -and $srcEngine.Class -eq $candEngine.Class) {
        $score += 25
    }
    if ($srcEngine.Family -and $candEngine.Family -and $srcEngine.Family -eq $candEngine.Family) {
        $score += 35
    }
    if ($srcWagon.Stock -and $candWagon.Stock -and $srcWagon.Stock -eq $candWagon.Stock) {
        $score += 30
    }
    if ($srcWagon.Coach -and $candWagon.Coach -and $srcWagon.Coach -eq $candWagon.Coach) {
        $score += 30
    }
    if ($srcWagon.Freight -and $candWagon.Freight -and $srcWagon.Freight -eq $candWagon.Freight) {
        $score += 25
    }
    if ($srcWagon.ContainerVendor -and $candWagon.ContainerVendor -and $srcWagon.ContainerVendor -eq $candWagon.ContainerVendor) {
        $score += 25
    }
    if ($srcWagon.SetHint -and $candWagon.SetHint -and $srcWagon.SetHint -eq $candWagon.SetHint) {
        $score += 8
    }
    if ($DebugScores) {
        Write-Log 'INFO' ("[DEBUG] TOKENS srcEng={0}/{1}/{2} srcWag={3}/{4}/{5}/{6} caboose={7}; candEng={8}/{9}/{10} candWag={11}/{12}/{13}/{14} caboose={15} score={16}" -f `
            $srcEngine.Class, $srcEngine.Series, $srcEngine.Family,
            $srcWagon.Stock, $srcWagon.Coach, $srcWagon.Freight, $srcWagon.ContainerVendor, $srcWagon.Caboose,
            $candEngine.Class, $candEngine.Series, $candEngine.Family,
            $candWagon.Stock, $candWagon.Coach, $candWagon.Freight, $candWagon.ContainerVendor, $candWagon.Caboose,
            $score)
    }
    # === End boosts ===
    return $score
}

function Guess-Type([string]$name,[string]$folder,[string]$kind){
    if($kind -eq 'Engine'){ return 'Engine' }
    $r = Get-WagonRole $name $folder
    switch($r){
        'Wagon:Caboose'   { return 'Caboose' }
        'Wagon:Container' { return 'Container' }
        'Wagon:EMU'       { return 'EMU' }
        'Wagon:Parcel'    { return 'Parcel' }
        default           { return 'Wagon' }
    }
}

# -------------------------------------------------------------------------------------
# Candidate search
# -------------------------------------------------------------------------------------
function Find-Candidates-Local([string]$kind,[string]$shape,[string]$folder){
    $folderU = $folder
    $norm    = Normalize-Name $shape
    $pool    = if($Index.ByFolder.ContainsKey($folderU)){ @($Index.ByFolder[$folderU] | Where-Object { $_.Kind -eq $kind }) } else { @() }
    if(-not $pool){ return @() }

    $cands = @()
    $exactKey = ($folderU.ToLowerInvariant() + '|' + $shape.ToLowerInvariant())
    if($Index.MapExact.ContainsKey($exactKey)){ $cands += ,$Index.MapExact[$exactKey] }
    if($Index.ByNorm.ContainsKey($norm)){
        $cands += @( $Index.ByNorm[$norm] | Where-Object { $_.Folder -eq $folderU -and $_.Kind -eq $kind } )
    }
    $cands += @( $pool | Where-Object { $_.Name -like "*$shape*" -or $_.Norm -like "*$norm*" } )
    $seen = @{}; $out = @()
    foreach($c in $cands){ if(-not $c){ continue }; $k = $c.Key; if(-not $seen.ContainsKey($k)){ $seen[$k] = $true; $out += ,$c } }
    return $out
}

function Find-Candidates-GlobalCache([string]$kind,[string]$shape,[string]$folder){
    $norm = Normalize-Name $shape
    $pool = if($kind -eq 'Engine'){ $Index.Engines } else { $Index.Wagons }
    $pool = @($pool | Where-Object { $_.Folder -ne $DefaultsFolderName })
    $cands = @()
    if($Index.ByNorm.ContainsKey($norm)){
        $cands += @( $Index.ByNorm[$norm] | Where-Object { $_.Kind -eq $kind -and $_.Folder -ne $DefaultsFolderName } )
    }
    $cands += @( $pool | Where-Object { $_.Name -like "*$shape*" -or $_.Norm -like "*$norm*" } )
    $seen = @{}; $out = @()
    foreach($c in $cands){ if(-not $c){ continue }; $k = $c.Key; if(-not $seen.ContainsKey($k)){ $seen[$k] = $true; $out += ,$c } }
    return $out
}

function Same-Role([string]$kind,[string]$shape,[string]$folder,[pscustomobject]$cand){
    if($kind -eq 'Engine'){
        $need = Get-EngineRole $shape $folder
        $have = Get-EngineRole $cand.Name $cand.Folder
        if($need -eq 'Engine:Unknown' -or $have -eq 'Engine:Unknown'){ return $true }
        return ($need -eq $have)
    } else {
        $need = Get-WagonRole $shape $folder
        $have = Get-WagonRole $cand.Name $cand.Folder
        if($need -eq 'Wagon:Unknown' -or $have -eq 'Wagon:Unknown'){ return $true }
        return ($need -eq $have)
    }
}

# Defaults selection (with safe sorts)
function From-Defaults-Engine([string]$shape,[string]$folder){
    if(-not $Index.ByFolder.ContainsKey($DefaultsFolderName)){ return $null }
    $wantRole  = Get-EngineRole $shape $folder
    $wantClass = Get-EngineClass $shape $folder
    $pool = @($Index.ByFolder[$DefaultsFolderName] | Where-Object { $_.Kind -eq 'Engine' })
    if(-not $pool){ return $null }
    $cands = @($pool | Where-Object { (Get-EngineClass $_.Name $_.Folder) -eq $wantClass })
    if(-not $cands){ $cands = $pool }
    $cands = @($cands | Where-Object { $wantRole -eq 'Engine:Unknown' -or (Get-EngineRole $_.Name $_.Folder) -eq $wantRole })
    if(-not $cands){ return $null }
    return ($cands | Sort-Object -Property { (Score-Candidate $shape $folder $_) } -Descending | Select-Object -First 1)
}
function From-Defaults-Wagon([string]$shape,[string]$folder){
    if(-not $Index.ByFolder.ContainsKey($DefaultsFolderName)){ return $null }
    $wantRole = Get-WagonRole $shape $folder
    $wantType = (Get-CoachType $shape); if(-not $wantType){ $wantType = Get-CoachType $folder }
    $pool = @($Index.ByFolder[$DefaultsFolderName] | Where-Object { $_.Kind -eq 'Wagon' })
    if(-not $pool){ return $null }
    $cands = @($pool | Where-Object { $wantRole -eq 'Wagon:Unknown' -or (Get-WagonRole $_.Name $_.Folder) -eq $wantRole })
    if(-not $cands){ return $null }
    if($wantRole -eq 'Wagon:Passenger' -and $wantType){
        $pref = @($cands | Where-Object {
            ($_.Name.ToUpperInvariant() -match $wantType) -or ((Get-CoachType $_.Name) -eq $wantType) -or ((Get-CoachType $_.Folder) -eq $wantType)
        })
        if($pref.Count -gt 0){ $cands = $pref }
    }
    return ($cands | Sort-Object -Property { (Score-Candidate $shape $folder $_) } -Descending | Select-Object -First 1)
}

function Apply-StrictPrefs([string]$kind,[string]$shape,[string]$folder,[pscustomobject[]]$cands){
    $out = $cands
    if($kind -eq 'Engine' -and $StrictEngineClass -and $out.Count){
        $want = Get-EngineClass $shape $folder
        if($want){
            $filtered = @($out | Where-Object { (Get-EngineClass $_.Name $_.Folder) -eq $want })
            if($filtered.Count -gt 0){ $out = $filtered } else { Write-Log 'INFO' "StrictEngineClass relaxed for '$shape' (no class=$want found)." }
        }
    }
    if($kind -eq 'Wagon' -and $StrictWagonType -and $out.Count){
        $want = (Get-CoachType $shape); if(-not $want){ $want = Get-CoachType $folder }
        if($want){
            $filtered = @($out | Where-Object { ($_.Name.ToUpperInvariant() -match $want) -or ((Get-CoachType $_.Name) -eq $want) -or ((Get-CoachType $_.Folder) -eq $want) })
            if($filtered.Count -gt 0){ $out = $filtered } else { Write-Log 'INFO' "StrictWagonType relaxed for '$shape' (no coachType=$want found)." }
        }
    }
    return ,$out
}

function Log-Candidates($stage,$kind,$shape,$folder,$cands){
    if(-not $DebugScores -or -not $cands){ return }
    $top = $cands |
        Sort-Object -Property { (Score-Candidate $shape $folder $_) } -Descending |
        Select-Object -First 3 |
        ForEach-Object {
            $s = Score-Candidate $shape $folder $_
            "{0}[{1}] score={2}" -f $_.Name,$_.Folder,$s
        }
    if($top){ Write-Log 'INFO' ("{0} top: {1}" -f $stage, ($top -join ' | ')) }
}

function Resolve-Asset([string]$kind,[string]$shape,[string]$folder){
    $shapeU  = $shape.Trim('"')
    $folderU = $folder.Trim('"')

    $wantRole  = if($kind -eq 'Engine'){ Get-EngineRole $shapeU $folderU } else { Get-WagonRole $shapeU $folderU }
    $wantClass = if($kind -eq 'Engine'){ Get-EngineClass $shapeU $folderU } else { (Get-CoachType $shapeU) }
    Write-Log 'INFO' ("Need {0}: class={1} role={2} for '{3}' in '{4}'" -f $kind,$wantClass,$wantRole,$shapeU,$folderU)

    # 1) LOCAL
    $local = @( Find-Candidates-Local $kind $shapeU $folderU )
    # --- FAST-PATH: exact and 2-token overlap (Local & Global) ---
    $globFP = @( Find-Candidates-GlobalCache $kind $shapeU $folderU )
    if($globFP.Count){
        $globFP = @($globFP | Where-Object { Same-Role $kind $shapeU $folderU $_ })
        $globFP = @( Apply-StrictPrefs $kind $shapeU $folderU $globFP )
    }
    $fp = Try-FastPath $kind $shapeU $folderU $local $globFP
    if($fp){
        return $fp
    }
    # --- END FAST-PATH ---

    if($local.Count){
        $local = @($local | Where-Object { Same-Role $kind $shapeU $folderU $_ })
        $local = @( Apply-StrictPrefs $kind $shapeU $folderU $local )
    }
    Log-Candidates "LOCAL" $kind $shapeU $folderU $local
    # --- FAST-PATH: exact and 2-token overlap (Local & Global) ---
    $globFP = @( Find-Candidates-GlobalCache $kind $shapeU $folderU )
    if($globFP.Count){
        $globFP = @($globFP | Where-Object { Same-Role $kind $shapeU $folderU $_ })
        $globFP = @( Apply-StrictPrefs $kind $shapeU $folderU $globFP )
    }
    $fp = Try-FastPath $kind $shapeU $folderU $local $globFP
    if($fp){
        return $fp
    }
    # --- END FAST-PATH ---
    
    if($local.Count){
        $bestLocal = $local | Sort-Object -Property { (Score-Candidate $shapeU $folderU $_) } -Descending | Select-Object -First 1
        $bestLocalScore = Score-Candidate $shapeU $folderU $bestLocal
        if($bestLocalScore -ge $MinLocalScore){
            $bestLocal = $bestLocal | Select-Object *
            $bestLocal | Add-Member -Force -NotePropertyName 'Source' -NotePropertyValue 'Local' -PassThru | Out-Null
            $bestLocal | Add-Member -Force -NotePropertyName 'Score'  -NotePropertyValue $bestLocalScore -PassThru | Out-Null
            return $bestLocal
        } else {
            Write-Log 'INFO' ("Local candidates too weak for '{0}' in '{1}' (bestScore={2} < MinLocalScore={3})." -f $shapeU,$folderU,$bestLocalScore,$MinLocalScore)
        }
    } else {
        Write-Log 'INFO' "No local candidates for '$shapeU' in '$folderU'."
    }

    # 2) GLOBAL (exclude _DEFAULTS)
    $glob = @( Find-Candidates-GlobalCache $kind $shapeU $folderU )
    if($glob.Count){
        $glob = @($glob | Where-Object { Same-Role $kind $shapeU $folderU $_ })
        $glob = @( Apply-StrictPrefs $kind $shapeU $folderU $glob )
    }
    Log-Candidates "GLOBAL" $kind $shapeU $folderU $glob
    if($glob.Count){
        $bestGlob = $glob | Sort-Object -Property { (Score-Candidate $shapeU $folderU $_) } -Descending | Select-Object -First 1
        $bestGlobScore = Score-Candidate $shapeU $folderU $bestGlob
        if($bestGlobScore -ge $MinCacheScore){
            $bestGlob = $bestGlob | Select-Object *
            $bestGlob | Add-Member -Force -NotePropertyName 'Source' -NotePropertyValue 'Global' -PassThru | Out-Null
            $bestGlob | Add-Member -Force -NotePropertyName 'Score'  -NotePropertyValue $bestGlobScore -PassThru | Out-Null
            return $bestGlob
        } else {
            Write-Log 'INFO' ("Global candidates too weak for '{0}' (bestScore={1} < MinCacheScore={2})." -f $shapeU,$bestGlobScore,$MinCacheScore)
        }
    } else {
        Write-Log 'INFO' "No global cache candidates for '$shapeU'."
    }

    # 3) DEFAULTS
    if($kind -eq 'Engine'){
        $d = From-Defaults-Engine $shapeU $folderU
        if($d){ $d = $d | Select-Object *; $d | Add-Member -Force -NotePropertyName 'Source' -NotePropertyValue 'Defaults' -PassThru | Out-Null; return $d }
    } else {
        $d = From-Defaults-Wagon $shapeU $folderU
        if($d){ $d = $d | Select-Object *; $d | Add-Member -Force -NotePropertyName 'Source' -NotePropertyValue 'Defaults' -PassThru | Out-Null; return $d }
    }
    return $null
}

# -------------------------------------------------------------------------------------
# .con parsing & rewrite
# -------------------------------------------------------------------------------------
$ConRegex = [regex]'(?ix) ^ \s*(?<pad>\s*)(?<kw>EngineData|WagonData)\s*\(\s*"?(?<shape>[^\s"()]+)"?\s+(?<folder>[^)]*?)\s*\)'
$ConRegexLoose = [regex]'(?ix)(?<kw>EngineData|WagonData)\s*\(\s*"?(?<shape>[^\s"()]+)"?\s+(?<folder>[^)]+?)\s*\)'

# Counters
$global:Fixed = 0; $global:Unchanged = 0; $global:NoMatch = 0

function Process-Con([string]$conPath){
    $name = Split-Path $conPath -Leaf
	$conShort = [System.IO.Path]::GetFileNameWithoutExtension($name)
    $text = Get-Content -LiteralPath $conPath -Raw -Encoding UTF8 -ErrorAction Stop
    $changed = $false

    $outLines = $text -split "`n" | ForEach-Object {
    $line = [string]$PSItem

    $m = $ConRegex.Match($line)
    if (-not $m.Success) { return $line }

    $kw     = $m.Groups['kw'].Value
    $pad    = $m.Groups['pad'].Value
    $folder = $m.Groups['folder'].Value.Trim().Trim('"')
    $shape  = $m.Groups['shape'].Value.Trim().Trim('"')
    $kind   = if ($kw -eq 'EngineData') { 'Engine' } else { 'Wagon' }

    $tShape = Normalize-Name $shape
    if ($tShape -match '\b(ai[\-_]?\s*horn|horn|sound)\b') {
        $global:Unchanged++
        return $line
    }

    $key = ($folder.ToLowerInvariant() + '|' + $shape.ToLowerInvariant())
    if ($Index.MapExact.ContainsKey($key)) {
        $global:Unchanged++
        return $line
    }

    $resolved = Resolve-Asset $kind $shape $folder

    if ($null -ne $resolved) {
        $newLine = "{0}{1} ( {2} ""{3}"" )" -f $pad, $kw, $resolved.Folder, $resolved.Name
        $global:Fixed++

        $src   = if ($resolved.PSObject -and $resolved.PSObject.Properties['Source']) { $resolved.Source } else { 'Unknown' }
        $score = if ($resolved.PSObject -and $resolved.PSObject.Properties['Score'])  { try { [int]$resolved.Score } catch { -1 } } else { -1 }

        $classInfo = ''
        if ($kind -eq 'Engine') {
            $classInfo = Get-EngineClass $shape $folder
        } else {
            $ct = Get-CoachType $folder $shape
            if ($ct) { $classInfo = "CoachType=$ct" }
        }

        Write-Log 'INFO' ("{0}: {1} ({2} {3}) -> REPLACED with {4}/{5}  [Source:{6} Score:{7} {8}]" -f `
            $conShort, $kw, $folder, $shape, $resolved.Folder, $resolved.Name, $src, $score, $classInfo)

        return $newLine
    }

    
    # Token-map suggestion before NO MATCH
    try {
        $tm = TM-GetTokenMaps -CachePath $CachePath
        $sugs = TM-Suggest -TokenMaps $tm -Section $kw -OldShape $shape -OldFolder $folder -Top 1 -MinScore 0.35
        if ($sugs -and $sugs[0].Score -ge 0.45) {
            $cand = $sugs[0].Cand
            $resolved = [pscustomobject]@{ Name = [string]$cand.Name; Folder = [string]$cand.Folder }
            $src = 'Global'; $score = [int]([math]::Round($sugs[0].Score*100,0))

            # build replacement line (reuse existing logic variables $pad,$kw)
            $newLine = "{0}{1} ( {2} ""{3}"" )" -f $pad, $kw, $resolved.Name, $resolved.Folder

            # use existing logging method
            $classInfo = ''
            if ($kw -eq 'EngineData') {
                $classInfo = Get-EngineClass $shape $folder
            } else {
                $ct = Get-CoachType $folder $shape
                if ($ct) { $classInfo = "CoachType=$ct" }
            }
            Write-ReplaceLog -ConName $conShort -Section $kw `
                -OrigId $shape -OrigFolder $folder -NewId $resolved.Name -NewFolder $resolved.Folder `
                -Source $src -Score $score -ClassInfo $classInfo

            $changed = $true
            return $newLine
        }
    } catch {
        # ignore suggestion errors; fall through to NO MATCH
    }

$global:NoMatch++
    
}
    # Final normalization pass: enforce quoted folder names and correct ordering (inline)
    $normalizedLines = @()
    foreach($origLine in $outLines){
        $m2 = $ConRegexLoose.Match([string]$origLine)
        if ($m2.Success) {
            $kw2     = $m2.Groups['kw'].Value
            $shape2  = $m2.Groups['shape'].Value.Trim().Trim('"')
            $folder2 = $m2.Groups['folder'].Value.Trim().Trim('"')
            $pad2    = ([regex]::Match([string]$origLine, '^\s*')).Value
            $normLine = ("{0}{1} ( {2} ""{3}"" )" -f $pad2, $kw2, $shape2, $folder2)
            if ($LogChanges -and $normLine -ne $origLine) {
                $global:Fixed++
                Write-ReplaceLog -ConName $conShort -Section $kw2 `
                    -OrigId $shape2 -OrigFolder $folder2 -NewId $shape2 -NewFolder $folder2 `
                    -Source 'Normalize' -Score -1 -ClassInfo ''
            }
            $normalizedLines += ,$normLine
        } else {
            $normalizedLines += ,$origLine
        }
    }
    $finalText = ($normalizedLines -join "`n")
    if ($finalText -ne $text) { $changed = $true }



    if($changed -and -not $DryRun){
        $finalText | Set-Content -LiteralPath $conPath -Encoding UTF8
    } elseif($changed -and $DryRun) {
        Write-Log 'INFO' "DryRun: $conPath would be modified."
    }
}

# -------------------------------------------------------------------------------------
# MAIN
# -------------------------------------------------------------------------------------
Write-Log 'INFO' "Consists folder: $ConsistsPath"
Write-Log 'INFO' "TrainsetDir:     $TrainsetDir"

$loaded = $false
if($UseCache){ $loaded = Load-Index }
if(-not $loaded){ Build-Index; if($UseCache){ Save-Index } }

$cons  = Get-ChildItem -LiteralPath $ConsistsPath -Filter '*.con' -ErrorAction Stop | Where-Object { -not $_.PSIsContainer }
$total = $cons.Count
Write-Log 'INFO' ("Found .con:      {0}" -f $total)

# Live progress
$i = 0
foreach($c in $cons){
    $i++
    Write-Progress -Activity "Processing consists" -Status "$i / $total : $($c.Name)" -PercentComplete ([int](100*$i/$total))
    Process-Con $c.FullName
}

Write-Log 'INFO' ("SUMMARY: fixed={0}, ok={1}, no_match={2}" -f $global:Fixed,$global:Unchanged,$global:NoMatch)
Write-Host "`n===== DONE ====="
Write-Output '===== TOTALS ====='
Write-Output ("fixed={0}, ok={1}, no_match={2}" -f $global:Fixed,$global:Unchanged,$global:NoMatch)
