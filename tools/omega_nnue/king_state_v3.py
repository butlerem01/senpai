#!/usr/bin/env python3
"""Fail-closed generation-3 data and pre-label orchestration.

This wrapper deliberately reuses the already exercised ``deep_hce_v2``
teacher machinery while narrowing it to the preregistered deep-hce-v4
experiment.  The wrapper owns every public path and policy value: callers
cannot select a historical source, change a seed/quota/cap, or name a
different output.

Prior JSON/JSONL artifacts are scanned with a lexical walker.  It decodes
object keys and OFEN-family string values only; all target and score values
are validated and skipped without materialization.  Teacher search remains
unreachable until the initializer, exclusion inventory, phase-incidence
audits, feature gates, and compatibility pre-label seal all verify.
"""

from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
from types import MappingProxyType
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence

# Proposal and verification commands are target-free read-only processes.
# Suppress local bytecode cache writes before importing repository modules.
sys.dont_write_bytecode = True

import king_state_train_generation3 as generation3_trainer
import deep_hce_v2 as deep
import phase_incidence_preflight as incidence
from omega_nnue import (
    KING_BUCKET_COUNT,
    KING_STATE_OCCUPANCY_FEATURES,
    parse_ofen,
)


SCHEMA_VERSION = 1
PROFILE_ID = "king-state-v3-deep-hce-v4"
SEED = 2026072104
SPLIT_SEED = 4989
TRAIN_PERCENT = 80.0
VALIDATION_PERCENT = 10.0
TARGET_PAIRS = 8192
TARGET_PAIRS_PER_PHASE = 2048
RESERVE_PAIRS_PER_PHASE = 128
PREFLIGHT_EXTRA_PAIRS_PER_PHASE = 64
MAX_PREFLIGHT_REJECTED_PAIRS = 32
MAX_PAIRS_PER_TRAJECTORY = 32
MAX_PAIRS_PER_SPLIT_GROUP = 64
CANDIDATE_PAIRS_PER_TRAJECTORY_PHASE = 64
ACCEPTANCE_TIMEOUT_SECONDS = 5.0
TEACHER_NODES = 100000
TRAJECTORY_PAIRS = 4096
PHASES = ("opening", "middlegame", "late", "endgame")
EXPECTED_PRIOR_ARTIFACT_COUNT = 753
EXPECTED_PRIOR_ARTIFACT_BYTES = 3468181998
EXPECTED_PRIOR_ORDERED_IDENTITY_SET: dict[str, Any] | None = {'bytes': 212519,
 'sha256': 'b56972cacf23fa26e076d3c0bcc795fe2fe3504c5db875597b4d97e5f2e657a2'}
OPENING_REPLAY_TIMEOUT_SECONDS = 3600
EXPECTED_COMPLEMENT_ARTIFACT_COUNT = 483
EXPECTED_COMPLEMENT_ARTIFACT_BYTES = 7_592_386
EXPECTED_COMPLEMENT_IDENTITY_SET = {
    "bytes": 140461,
    "sha256": (
        "16f582ae49b923ff753e4b08a649ecd9516e93d4b69a3214a162"
        "8d13f5f22ab0"
    ),
}
EXPECTED_COMPLEMENT_CLASSIFIED_IDENTITY_SET = {
    "bytes": 9641,
    "sha256": (
        "43dc5ee3ae5f5eb42f75c100048f188cbd113b8833b91c1cd48"
        "81638193293f4"
    ),
}
EXPECTED_COMPLEMENT_KEY_HITS = {
    "moves": 630,
    "openings": 26,
    "positions": 2,
}
EXPECTED_COMPLEMENT_RAW_BOARD_SHAPE_HITS = 6
EXPECTED_COMPLEMENT_CLASS_COUNTS = {
    "generated-canonical-public-opening-copy": 22,
    "generated-style-public-opening-copy": 4,
    "generated-omega-rules-fixture-copy": 2,
    "translation-en-core": 2,
    "translation-it-core": 2,
    "translation-en-omega": 1,
    "translation-it-omega": 1,
}
EXPECTED_REPLAY_AUDIT = {'completeSequenceDeduplicatedPostMovePrefixOccurrenceCount': 276,
 'completeSequenceDeduplicatedRootAndPrefixOccurrenceCount': 18209,
 'initialSourceCounts': {'explicit': 31037, 'official-default': 56},
 'legacyFourFieldBoardKeyArityCountEntries': [{'field': 'positionkey',
                                               'inputFieldCount': 4,
                                               'occurrences': 821},
                                              {'field': 'positionkey',
                                               'inputFieldCount': 6,
                                               'occurrences': 68817}],
 'legacyFourFieldBoardKeyProjection': {'bytes': 534942,
                                       'sha256': 'fa426daeffcb0cd759e9722033909a6de97da77452d5462c1ebe65ec4b21f9fe'},
 'legacyFourFieldBoardKeyReconciliation': {'acceptedScalarOccurrences': 69638,
                                           'field': 'positionkey',
                                           'nullOccurrences': 0,
                                           'passed': True,
                                           'structuralAliasOccurrences': 1,
                                           'triggerOccurrences': 69639},
 'legacyFourFieldBoardKeySourceEntries': [{'bytes': 54120,
                                           'occurrences': 84,
                                           'sha256': 'e2cc36abdaf49b5608063bdba8081f916cb233ad0f872fff436e477d5078ed90',
                                           'workspaceRelativePath': 'omega-lab/data/personal-smoke/endgame-positions.json'},
                                          {'bytes': 471102,
                                           'occurrences': 737,
                                           'sha256': 'bb688cde57f4f7b1af53835261824355d9d4695351f022f373e81abf2ff75a57',
                                           'workspaceRelativePath': 'omega-lab/data/smoke/endgame-positions.json'}],
 'newlyImplicitOpeningOccurrenceCount': 1980,
 'nullScalarValueCount': 1,
 'nullScalarValueCounts': {'postofen': 1},
 'openingDefaultInitialOccurrenceCount': 56,
 'openingExplicitInitialOccurrenceCount': 29058,
 'openingPostMovePrefixRecordCount': 1924,
 'openingPrefixRecordCount': 31038,
 'openingRequestCount': 29114,
 'partialOfenCanonicalizationCount': 821,
 'partialOfenCanonicalizationCounts': {'positionkey': 821},
 'prefixRecordCount': 284910,
 'requestCount': 31093,
 'requestSchemaCounts': {'event-opening-moves': 2321,
                         'schedule-moves': 26793,
                         'transcript': 1979},
 'structuralScalarAliasCount': 24,
 'structuralScalarAliasCounts': {'capability-board-check-result': 3,
                                 'inventory-board-class-rationale': 10,
                                 'report-board-field-schema': 1,
                                 'report-optional-board-field-schema': 1,
                                 'runtime-board-parser-source-identity': 2,
                                 'runtime-board-parser-source-path': 2,
                                 'runtime-board-state-source-identity': 2,
                                 'runtime-board-state-source-path': 2,
                                 'suite-board-field-schema': 1},
 'transcriptMoveCount': 251893,
 'transcriptPrefixRecordCount': 253872,
 'transcriptRequestCount': 1979,
 'uniqueOpeningPostMovePrefixIdentityCount': 200,
 'uniqueOpeningPrefixIdentityCount': 18062,
 'uniqueOpeningSequenceCount': 17933,
 'uniqueOpeningSequenceMoveLengthCounts': {'0': 17862, '2': 4, '4': 67}}
EXPECTED_FINAL_TARGET_FREE_INVENTORY: dict[str, Any] | None = {'authoritativeUnion': {'artifactBytesDecimal': '00000000003468181998',
                        'artifactCountDecimal': '00000000000000000753',
                        'stableArtifactCountDecimal': '00000000000000000753'},
 'boardStateEvidence': {'aggregateConservativeSignatureCardinalityDecimal': '00000000000000522131',
                        'aggregateExclusionOccurrenceCountDecimal': '00000000000002906587',
                        'aggregateNormalizedCardinalityDecimal': '00000000000000349557',
                        'generatedReplayConservativeSignatureCardinalityDecimal': '00000000000000236591',
                        'generatedReplayNormalizedCardinalityDecimal': '00000000000000137197',
                        'newlyImplicitOpeningOccurrenceCountDecimal': '00000000000000001980',
                        'rawDecodedConservativeSignatureCardinalityDecimal': '00000000000000522131',
                        'rawDecodedNormalizedCardinalityDecimal': '00000000000000349557',
                        'rawDecodedOccurrenceCountDecimal': '00000000000002904607'},
 'complementEvidence': {'artifactBytesDecimal': '00000000000007592386',
                        'artifactCountDecimal': '00000000000000000483',
                        'classifiedHitArtifactCountDecimal': '00000000000000000034',
                        'classifiedHitOrderedIdentitySet': {'bytesDecimal': '00000000000000009641',
                                                            'sha256': '43dc5ee3ae5f5eb42f75c100048f188cbd113b8833b91c1cd4881638193293f4'},
                        'orderedIdentitySet': {'bytesDecimal': '00000000000000140461',
                                               'sha256': '16f582ae49b923ff753e4b08a649ecd9516e93d4b69a3214a1628d13f5f22ab0'},
                        'rawBoardStringShapeHitCountDecimal': '00000000000000000006',
                        'triggerKeyCounts': [{'field': 'moves',
                                              'occurrencesDecimal': '00000000000000000630'},
                                             {'field': 'openings',
                                              'occurrencesDecimal': '00000000000000000026'},
                                             {'field': 'positions',
                                              'occurrencesDecimal': '00000000000000000002'}],
                        'unclassifiedHitArtifactCountDecimal': '00000000000000000000'},
 'informationBoundary': {'historicalEvidenceOnly': True,
                         'targetFieldsDecoded': 0,
                         'targetFieldsEmitted': 0},
 'kind': 'omega-nnue-king-state-v3-final-target-free-inventory',
 'legacyFourFieldBoardKeyCounts': [{'field': 'positionkey',
                                    'occurrencesDecimal': '00000000000000000821'}],
 'legacyFourFieldBoardKeyEvidence': {'arityCountEntries': [{'field': 'positionkey',
                                                            'inputFieldCount': 4,
                                                            'occurrencesDecimal': '00000000000000000821'},
                                                           {'field': 'positionkey',
                                                            'inputFieldCount': 6,
                                                            'occurrencesDecimal': '00000000000000068817'}],
                                     'countDecimal': '00000000000000000821',
                                     'orderedExpansionEventProjection': {'bytesDecimal': '00000000000000534942',
                                                                         'sha256': 'fa426daeffcb0cd759e9722033909a6de97da77452d5462c1ebe65ec4b21f9fe'},
                                     'reconciliation': {'acceptedScalarOccurrencesDecimal': '00000000000000069638',
                                                        'field': 'positionkey',
                                                        'nullOccurrencesDecimal': '00000000000000000000',
                                                        'passed': True,
                                                        'structuralAliasOccurrencesDecimal': '00000000000000000001',
                                                        'triggerOccurrencesDecimal': '00000000000000069639'},
                                     'sourceEntries': [{'bytesDecimal': '00000000000000054120',
                                                        'occurrencesDecimal': '00000000000000000084',
                                                        'sha256': 'e2cc36abdaf49b5608063bdba8081f916cb233ad0f872fff436e477d5078ed90',
                                                        'workspaceRelativePath': 'omega-lab/data/personal-smoke/endgame-positions.json'},
                                                       {'bytesDecimal': '00000000000000471102',
                                                        'occurrencesDecimal': '00000000000000000737',
                                                        'sha256': 'bb688cde57f4f7b1af53835261824355d9d4695351f022f373e81abf2ff75a57',
                                                        'workspaceRelativePath': 'omega-lab/data/smoke/endgame-positions.json'}]},
 'nullScalarCounts': [{'field': 'postofen',
                       'occurrencesDecimal': '00000000000000000001'}],
 'replayEvidence': {'fullParityMismatchCountDecimal': '00000000000000000000',
                    'openingPrefixRecordCountDecimal': '00000000000000031038',
                    'openingRequestCountDecimal': '00000000000000029114',
                    'outputIdentity': {'bytesDecimal': '00000000000170092824',
                                       'sha256': 'e0bc1d589316d8c89f8c70a9d88eb8f20420b946b45b17771e9ecd9ef0940728'},
                    'parseOrReplayErrorCountDecimal': '00000000000000000000',
                    'prefixRecordCountDecimal': '00000000000000284910',
                    'projectionIdentity': {'bytesDecimal': '00000000000041432717',
                                           'sha256': 'ce0267614102b88758f2c65d66970963a4a480b6736bdae17e09fbd051bd8908'},
                    'requestCountDecimal': '00000000000000031093',
                    'transcriptPrefixRecordCountDecimal': '00000000000000253872',
                    'transcriptRequestCountDecimal': '00000000000000001979',
                    'uniqueOpeningPostMovePrefixIdentityCountDecimal': '00000000000000000200',
                    'uniqueOpeningPrefixIdentityCountDecimal': '00000000000000018062'},
 'schemaVersion': 1,
 'structuralScalarAliases': [{'field': 'capability-board-check-result',
                              'occurrencesDecimal': '00000000000000000003'},
                             {'field': 'inventory-board-class-rationale',
                              'occurrencesDecimal': '00000000000000000010'},
                             {'field': 'report-board-field-schema',
                              'occurrencesDecimal': '00000000000000000001'},
                             {'field': 'report-optional-board-field-schema',
                              'occurrencesDecimal': '00000000000000000001'},
                             {'field': 'runtime-board-parser-source-identity',
                              'occurrencesDecimal': '00000000000000000002'},
                             {'field': 'runtime-board-parser-source-path',
                              'occurrencesDecimal': '00000000000000000002'},
                             {'field': 'runtime-board-state-source-identity',
                              'occurrencesDecimal': '00000000000000000002'},
                             {'field': 'runtime-board-state-source-path',
                              'occurrencesDecimal': '00000000000000000002'},
                             {'field': 'suite-board-field-schema',
                              'occurrencesDecimal': '00000000000000000001'}],
 'triggerKeyCounts': [{'field': 'active-match-position',
                       'occurrencesDecimal': '00000000000000000001'},
                      {'field': 'additionalexcludedpositionsread',
                       'occurrencesDecimal': '00000000000000000001'},
                      {'field': 'additionalscalarofenfields',
                       'occurrencesDecimal': '00000000000000000001'},
                      {'field': 'candidatepositions',
                       'occurrencesDecimal': '00000000000000000001'},
                      {'field': 'candidatepositionsbeforeexclusion',
                       'occurrencesDecimal': '00000000000000000005'},
                      {'field': 'candidatepositionsconsidered',
                       'occurrencesDecimal': '00000000000000000007'},
                      {'field': 'canonicalrootofen',
                       'occurrencesDecimal': '00000000000000000002'},
                      {'field': 'caseinsensitivescalarofenfields',
                       'occurrencesDecimal': '00000000000000000001'},
                      {'field': 'clear_hash_each_position',
                       'occurrencesDecimal': '00000000000000000001'},
                      {'field': 'conflictpositioncount',
                       'occurrencesDecimal': '00000000000000000001'},
                      {'field': 'continuationpositioncount',
                       'occurrencesDecimal': '00000000000000000001'},
                      {'field': 'directarraystringleavesdecodedasofencandidates',
                       'occurrencesDecimal': '00000000000000000001'},
                      {'field': 'disposition',
                       'occurrencesDecimal': '00000000000000000001'},
                      {'field': 'eligiblecandidatepositions',
                       'occurrencesDecimal': '00000000000000000001'},
                      {'field': 'eligiblepositionsbeforeboardorbitdeduplication',
                       'occurrencesDecimal': '00000000000000000003'},
                      {'field': 'eligiblepositionsbeforeorbitdeduplication',
                       'occurrencesDecimal': '00000000000000000002'},
                      {'field': 'endgamepositions',
                       'occurrencesDecimal': '00000000000000000006'},
                      {'field': 'excludedpositionartifacts',
                       'occurrencesDecimal': '00000000000000000002'},
                      {'field': 'excludeeverygeneration1andgeneration2heldoutposition',
                       'occurrencesDecimal': '00000000000000000001'},
                      {'field': 'excludeeverygeneration1andgeneration2trainingposition',
                       'occurrencesDecimal': '00000000000000000001'},
                      {'field': 'excludeeverygeneration1andgeneration2validationposition',
                       'occurrencesDecimal': '00000000000000000001'},
                      {'field': 'excludeeverypositionbearingartifactdiscoverablebeforefreeze',
                       'occurrencesDecimal': '00000000000000000001'},
                      {'field': 'excludeeverypriornnuematchposition',
                       'occurrencesDecimal': '00000000000000000001'},
                      {'field': 'excludeeverypriornnuescreenandconfirmationposition',
                       'occurrencesDecimal': '00000000000000000001'},
                      {'field': 'featurerowsmaybederivedfromofen',
                       'occurrencesDecimal': '00000000000000000001'},
                      {'field': 'fen',
                       'occurrencesDecimal': '00000000000000000229'},
                      {'field': 'fenheader',
                       'occurrencesDecimal': '00000000000000000004'},
                      {'field': 'finalofen',
                       'occurrencesDecimal': '00000000000000002321'},
                      {'field': 'finalpositioncount',
                       'occurrencesDecimal': '00000000000000000001'},
                      {'field': 'finalpositionsaccepted',
                       'occurrencesDecimal': '00000000000000000003'},
                      {'field': 'forbiddenpositionsread',
                       'occurrencesDecimal': '00000000000000000003'},
                      {'field': 'freshprocessperposition',
                       'occurrencesDecimal': '00000000000000000005'},
                      {'field': 'frozenregressionpositionsread',
                       'occurrencesDecimal': '00000000000000000001'},
                      {'field': 'historicalpositionroots',
                       'occurrencesDecimal': '00000000000000000001'},
                      {'field': 'initialofen',
                       'occurrencesDecimal': '00000000000000057153'},
                      {'field': 'invalid-ofen-or-ply',
                       'occurrencesDecimal': '00000000000000000087'},
                      {'field': 'invalidofencandidatecontributesnoexclusionsignature',
                       'occurrencesDecimal': '00000000000000000001'},
                      {'field': 'invalidofensignored',
                       'occurrencesDecimal': '00000000000000000005'},
                      {'field': 'labeledtrainingpositions',
                       'occurrencesDecimal': '00000000000000000001'},
                      {'field': 'largesttranspositioncomponent',
                       'occurrencesDecimal': '00000000000000000002'},
                      {'field': 'matchingordinarystringabortsasunclassifiedpotentialposition',
                       'occurrencesDecimal': '00000000000000000002'},
                      {'field': 'maxmovesperposition',
                       'occurrencesDecimal': '00000000000000000001'},
                      {'field': 'nestedarraystringleavesdecodedasofencandidates',
                       'occurrencesDecimal': '00000000000000000001'},
                      {'field': 'nodes_per_position',
                       'occurrencesDecimal': '00000000000000000001'},
                      {'field': 'normalizedofenexact',
                       'occurrencesDecimal': '00000000000000000004'},
                      {'field': 'ofen',
                       'occurrencesDecimal': '00000000000001992788'},
                      {'field': 'ofensequencesha256',
                       'occurrencesDecimal': '00000000000000000002'},
                      {'field': 'ofensha256',
                       'occurrencesDecimal': '00000000000000000096'},
                      {'field': 'officialinitialofen',
                       'occurrencesDecimal': '00000000000000000001'},
                      {'field': 'orbitdisjointfromeverygeneration1generation2andgeneration3trainingvalidationheldoutandpriormatchposition',
                       'occurrencesDecimal': '00000000000000000001'},
                      {'field': 'outputpositions',
                       'occurrencesDecimal': '00000000000000000001'},
                      {'field': 'phasederivedfromofen',
                       'occurrencesDecimal': '00000000000000000002'},
                      {'field': 'phasederivedfromofenpiececount',
                       'occurrencesDecimal': '00000000000000000001'},
                      {'field': 'position',
                       'occurrencesDecimal': '00000000000000003073'},
                      {'field': 'position.castling-rights',
                       'occurrencesDecimal': '00000000000000000002'},
                      {'field': 'position.champion-imbalance-not-one',
                       'occurrencesDecimal': '00000000000000000001'},
                      {'field': 'position.current-check',
                       'occurrencesDecimal': '00000000000000000006'},
                      {'field': 'position.en-passant-state',
                       'occurrencesDecimal': '00000000000000000002'},
                      {'field': 'position.halfmove-edge',
                       'occurrencesDecimal': '00000000000000000002'},
                      {'field': 'position.immediate-capture',
                       'occurrencesDecimal': '00000000000000000002'},
                      {'field': 'position.immediate-legal-capture',
                       'occurrencesDecimal': '00000000000000000004'},
                      {'field': 'position.immediate-legal-promotion',
                       'occurrencesDecimal': '00000000000000000001'},
                      {'field': 'position.immediate-promotion',
                       'occurrencesDecimal': '00000000000000000003'},
                      {'field': 'position.king-in-check',
                       'occurrencesDecimal': '00000000000000000001'},
                      {'field': 'position.no-matched-cross-identity-pair',
                       'occurrencesDecimal': '00000000000000000002'},
                      {'field': 'position.no-multitype-leaper-capture',
                       'occurrencesDecimal': '00000000000000000001'},
                      {'field': 'position.no-safe-champion-activation',
                       'occurrencesDecimal': '00000000000000000001'},
                      {'field': 'position.no-safe-wizard-activation',
                       'occurrencesDecimal': '00000000000000000001'},
                      {'field': 'position.no-same-champion-safe-option-gap-pair',
                       'occurrencesDecimal': '00000000000000000001'},
                      {'field': 'position.no-unmoved-original-champion',
                       'occurrencesDecimal': '00000000000000000002'},
                      {'field': 'position.no-unmoved-original-wizard',
                       'occurrencesDecimal': '00000000000000000001'},
                      {'field': 'position.one-or-fewer-legal-moves',
                       'occurrencesDecimal': '00000000000000000001'},
                      {'field': 'position.original-wizard-missing-or-mutated',
                       'occurrencesDecimal': '00000000000000000002'},
                      {'field': 'position.original-wizard-no-eligible-landing',
                       'occurrencesDecimal': '00000000000000000002'},
                      {'field': 'position.pawn-count-outside-p01-20',
                       'occurrencesDecimal': '00000000000000000001'},
                      {'field': 'position.phase-outside-u01-24',
                       'occurrencesDecimal': '00000000000000000001'},
                      {'field': 'position.prior-selected-board-orbit',
                       'occurrencesDecimal': '00000000000000000001'},
                      {'field': 'position.recorded-next-move-is-check',
                       'occurrencesDecimal': '00000000000000000001'},
                      {'field': 'position_index',
                       'occurrencesDecimal': '00000000000000000240'},
                      {'field': 'positionafter',
                       'occurrencesDecimal': '00000000000000010469'},
                      {'field': 'positionbefore',
                       'occurrencesDecimal': '00000000000000010469'},
                      {'field': 'positioncount',
                       'occurrencesDecimal': '00000000000000000001'},
                      {'field': 'positionheader',
                       'occurrencesDecimal': '00000000000000000004'},
                      {'field': 'positionhitpercent',
                       'occurrencesDecimal': '00000000000000000017'},
                      {'field': 'positionhits',
                       'occurrencesDecimal': '00000000000000000017'},
                      {'field': 'positionkey',
                       'occurrencesDecimal': '00000000000000069639'},
                      {'field': 'positionlikekeycompleteness',
                       'occurrencesDecimal': '00000000000000000001'},
                      {'field': 'positionrank',
                       'occurrencesDecimal': '00000000000000003068'},
                      {'field': 'positionrole',
                       'occurrencesDecimal': '00000000000001023076'},
                      {'field': 'positions',
                       'occurrencesDecimal': '00000000000000002111'},
                      {'field': 'positions_scanned',
                       'occurrencesDecimal': '00000000000000000001'},
                      {'field': 'positionsaccepted',
                       'occurrencesDecimal': '00000000000000000003'},
                      {'field': 'positionschecked',
                       'occurrencesDecimal': '00000000000000000033'},
                      {'field': 'positionsperphaseandside',
                       'occurrencesDecimal': '00000000000000000007'},
                      {'field': 'positionsperphaseside',
                       'occurrencesDecimal': '00000000000000000002'},
                      {'field': 'positionsprobed',
                       'occurrencesDecimal': '00000000000000000003'},
                      {'field': 'positionsrejected',
                       'occurrencesDecimal': '00000000000000000003'},
                      {'field': 'postofen',
                       'occurrencesDecimal': '00000000000000254166'},
                      {'field': 'preofen',
                       'occurrencesDecimal': '00000000000000252184'},
                      {'field': 'priorsuitecanonicalofensexcluded',
                       'occurrencesDecimal': '00000000000000000001'},
                      {'field': 'priorsuiteofensandreflectionsexcluded',
                       'occurrencesDecimal': '00000000000000000001'},
                      {'field': 'quieteligiblepositionsbeforededuplication',
                       'occurrencesDecimal': '00000000000000000001'},
                      {'field': 'recognizedofenkeys',
                       'occurrencesDecimal': '00000000000000000001'},
                      {'field': 'recognizedomegaofenshape',
                       'occurrencesDecimal': '00000000000000000002'},
                      {'field': 'rootreachablepositionpercent',
                       'occurrencesDecimal': '00000000000000000001'},
                      {'field': 'rootreachablepositions',
                       'occurrencesDecimal': '00000000000000000001'},
                      {'field': 'scientificdisposition',
                       'occurrencesDecimal': '00000000000000000001'},
                      {'field': 'searchdepthperposition',
                       'occurrencesDecimal': '00000000000000000003'},
                      {'field': 'senpaiofenacceptance',
                       'occurrencesDecimal': '00000000000000000003'},
                      {'field': 'sidetomovemaybederivedfromofen',
                       'occurrencesDecimal': '00000000000000000001'},
                      {'field': 'sourcecandidatepositions',
                       'occurrencesDecimal': '00000000000000000001'},
                      {'field': 'sourceofen',
                       'occurrencesDecimal': '00000000000000000121'},
                      {'field': 'sourceofencount',
                       'occurrencesDecimal': '00000000000000000120'},
                      {'field': 'sourceofens',
                       'occurrencesDecimal': '00000000000000000121'},
                      {'field': 'stopposition',
                       'occurrencesDecimal': '00000000000000000002'},
                      {'field': 'trainingpositions',
                       'occurrencesDecimal': '00000000000000000006'},
                      {'field': 'transpositioncomponents',
                       'occurrencesDecimal': '00000000000000000002'},
                      {'field': 'transpositionmerges',
                       'occurrencesDecimal': '00000000000000000001'}]}
OMEGA_INITIAL_OFEN = (
    "crnbqkbnrc/pppppppppp/10/10/10/10/10/10/"
    "PPPPPPPPPP/CRNBQKBNRC[W/W/w/w] w KQkq - 0 1"
)

REPO = Path(__file__).resolve().parents[2]
WORKSPACE = REPO.parent
FROZEN_RUNTIME = (
    REPO / "tools" / "omega_nnue" / "frozen_runtime" / "king-state-v3"
)
DATA_PARENT = REPO / "build-msvc" / "data-generation"
DATA_DIR = DATA_PARENT / "deep-hce-v4"
TRAINING_OUTPUT_DIR = REPO / "build-msvc" / "king-state-v3"
MATCH_PREPARATION_OUTPUT_DIR = REPO / "build-king-state-v3"
MATCH_RUN_OUTPUT_DIR = (
    WORKSPACE / "match-runs" / "output" / "king-state-v3"
)
GENERATION3_DESTINATION_DECLARATIONS = (
    (
        "teacher-data",
        "repository",
        "build-msvc/data-generation/deep-hce-v4",
        DATA_DIR,
    ),
    (
        "training-and-offline",
        "repository",
        "build-msvc/king-state-v3",
        TRAINING_OUTPUT_DIR,
    ),
    (
        "readiness-and-match-preparation",
        "repository",
        "build-king-state-v3",
        MATCH_PREPARATION_OUTPUT_DIR,
    ),
    (
        "match-results",
        "workspace",
        "match-runs/output/king-state-v3",
        MATCH_RUN_OUTPUT_DIR,
    ),
)
GENERATION3_DESTINATION_SUBTREES = tuple(
    path
    for _destination_id, _base, _relative_path, path
    in GENERATION3_DESTINATION_DECLARATIONS
)
RULES_ROOTS = DATA_PARENT / "deep-hce-v4-random-roots.jsonl"
RULES_MANIFEST = Path(str(RULES_ROOTS) + ".manifest.json")
RULES_FREEZE = Path(str(RULES_ROOTS) + ".freeze.json")
DEEP_LOCK = DATA_DIR / "deep-hce-v2.freeze.json"
DEEP_SUITE = DATA_DIR / "deep-hce-v2-suite.json"
RESULTS = DATA_DIR / "deep-hce-v2-results.jsonl"
SEARCH_CORPUS = DATA_DIR / "deep-hce-v2-search.jsonl"
SEARCH_MANIFEST = DATA_DIR / "deep-hce-v2-search.manifest.json"
HCE_CORPUS = DATA_DIR / "deep-hce-v2-handcrafted.jsonl"
HCE_MANIFEST = DATA_DIR / "deep-hce-v2-handcrafted.jsonl.manifest.json"
RESIDUAL_CORPUS = DATA_DIR / "deep-hce-v2-residual.jsonl"
RESIDUAL_MANIFEST = DATA_DIR / "deep-hce-v2-residual.jsonl.manifest.json"

PREREGISTRATION = (
    REPO / "validation" / "omega-nnue-king-state-v3-preregistration.json"
)
AMENDMENT = (
    REPO / "validation" / "omega-nnue-king-state-v3-amendment-001.json"
)
AMENDMENT_002 = (
    REPO / "validation" / "omega-nnue-king-state-v3-amendment-002.json"
)
AMENDMENT_003 = (
    REPO / "validation" / "omega-nnue-king-state-v3-amendment-003.json"
)
MATCH_PROTOCOL = (
    REPO / "validation" / "omega-nnue-king-state-v3-match-protocol.json"
)
MATCH_ADAPTER = (
    REPO / "validation" / "omega-nnue-king-state-v3-match-adapter.json"
)
GEN2_INCIDENT = (
    REPO / "validation" / "omega-nnue-king-state-v2-offline-incident.json"
)
INITIALIZER_RESOLUTION = (
    DATA_DIR / "generation3-initializer-resolution.seal.json"
)
EXCLUSION_CATALOG = DATA_DIR / "target-opaque-exclusion-catalog.json"
CANDIDATE_PROJECTION = DATA_DIR / "phase-incidence-candidate-pool.jsonl"
FINAL_ROOT_PROJECTION = DATA_DIR / "phase-incidence-final-roots.jsonl"
CANDIDATE_AUDIT = (
    DATA_DIR / "phase-incidence-candidate-pool.seal.json"
)
FINAL_ROOT_AUDIT = (
    DATA_DIR / "phase-incidence-final-roots.seal.json"
)
FINAL_SEARCH_AUDIT = (
    DATA_DIR / "phase-incidence-finalized-search.seal.json"
)
FINAL_SEARCH_FEATURE_AUDIT = (
    DATA_DIR / "feature-only-finalized-search-audit.json"
)
FINAL_RESIDUAL_AUDIT = DATA_DIR / "phase-incidence-preflight.seal.json"
PRECLAIM_AUDIT = DATA_DIR / "phase-incidence-preclaim.seal.json"
FEATURE_AUDIT = DATA_DIR / "feature-only-prelabel-audit.json"
CANDIDATE_UNIQUENESS_AUDIT = (
    DATA_DIR / "candidate-pool-global-uniqueness-audit.json"
)
FINAL_FEATURE_AUDIT = DATA_DIR / "feature-only-finalized-audit.json"
FREEZE_AUDIT = DATA_DIR / "generation3-freeze-audit.json"
CORPUS_VERIFICATION = DATA_DIR / "generation3-corpus-verification.json"
PRELABEL_SEAL = DATA_DIR / "king-state-v1-prelabel.seal.json"

ENGINE = FROZEN_RUNTIME / "engine" / "senpai.exe"
HARNESS = FROZEN_RUNTIME / "omegamatch" / "OmegaMatch.dll"
DOTNET = WORKSPACE / ".dotnet" / "dotnet.exe"
DOTNET_HOSTFXR_DIR = (
    WORKSPACE / ".dotnet" / "host" / "fxr" / "10.0.9"
)
DOTNET_HOSTFXR_SELECTION_DIR = DOTNET_HOSTFXR_DIR.parent
DOTNET_SHARED_RUNTIME_DIR = (
    WORKSPACE
    / ".dotnet"
    / "shared"
    / "Microsoft.NETCore.App"
    / "10.0.9"
)
DOTNET_SHARED_RUNTIME_SELECTION_DIR = DOTNET_SHARED_RUNTIME_DIR.parent
DOTNET_REQUIRED_ABSENT_PATHS = {
    "defaultSharedStore": DOTNET.parent / "store",
}
SAMPLER_PROJECT = (
    REPO / "tools" / "omega_nnue" / "OmegaRootSampler"
    / "OmegaRootSampler.csproj"
)
SAMPLER_SOURCE = SAMPLER_PROJECT.with_name("Program.cs")
ROOT_SAMPLER_RUNTIME = FROZEN_RUNTIME / "root-sampler"
ROOT_SAMPLER_ASSEMBLY = (
    ROOT_SAMPLER_RUNTIME / "OmegaRootSampler.dll"
)
HISTORY_SNAPSHOT_PROJECT = (
    REPO / "tools" / "omega_nnue" / "OmegaHistorySnapshot"
    / "OmegaHistorySnapshot.csproj"
)
HISTORY_SNAPSHOT_SOURCE = HISTORY_SNAPSHOT_PROJECT.with_name(
    "Program.cs"
)
HISTORY_SNAPSHOT_RUNTIME = FROZEN_RUNTIME / "history-snapshot"
HISTORY_SNAPSHOT_ASSEMBLY = (
    HISTORY_SNAPSHOT_RUNTIME / "OmegaHistorySnapshot.dll"
)
STATIC_HCE = FROZEN_RUNTIME / "evaluator" / "omega_nnue.exe"
OPENING_REPLAY_PROJECT = (
    REPO / "tools" / "omega_nnue" / "OmegaOpeningPrefixReplay"
    / "OmegaOpeningPrefixReplay.csproj"
)
OPENING_REPLAY_SOURCE = OPENING_REPLAY_PROJECT.with_name("Program.cs")
OPENING_REPLAY_RUNTIME = FROZEN_RUNTIME / "opening-replay"
OPENING_REPLAY_HELPER = (
    OPENING_REPLAY_RUNTIME / "OmegaOpeningPrefixReplay.dll"
)
OPENING_REPLAY_RULES = OPENING_REPLAY_RUNTIME / "ChessLib.dll"
OPENING_REPLAY_DEPS = (
    OPENING_REPLAY_RUNTIME / "OmegaOpeningPrefixReplay.deps.json"
)
OPENING_REPLAY_RUNTIMECONFIG = (
    OPENING_REPLAY_RUNTIME / "OmegaOpeningPrefixReplay.runtimeconfig.json"
)
OPENING_REPLAY_RULES_SOURCE = (
    WORKSPACE / "corechess-arena" / "ChessLib" / "Game.cs"
)
OPENING_REPLAY_RULES_PROJECT = (
    WORKSPACE / "corechess-arena" / "ChessLib" / "ChessLib.csproj"
)
CANONICAL_PUBLIC_OPENINGS = (
    WORKSPACE
    / "corechess-arena"
    / "Tools"
    / "OmegaMatch"
    / "Openings"
    / "omega-public-24.json"
)
CANONICAL_OMEGA_RULES_FIXTURE = (
    WORKSPACE / "fixtures" / "omega-rules.json"
)
STRUCTURAL_SCALAR_ALIAS_SOURCES = (
    {
        "path": str(
            REPO
            / "build-msvc"
            / "data-generation"
            / "deep-hce-v2"
            / "king-state-v1-prelabel.seal.json"
        ),
        "bytes": 18791,
        "sha256": (
            "901e70b538d6348524c9b4a94493d4f57c81c826d1c0ca9c36"
            "ed13455613a2f4"
        ),
        "aliases": (
            {
                "pointer": "/identities/runtime/fen",
                "field": "fen",
                "role": "runtime-board-parser-source-identity",
                "valueKind": "object",
                "canonicalBytes": 210,
                "canonicalSha256": (
                    "bf86ca5b487a6f858e3a16452c30a12b77b7de21bf4efea80c"
                    "362ca16231ceb7"
                ),
            },
            {
                "pointer": "/identities/runtime/position",
                "field": "position",
                "role": "runtime-board-state-source-identity",
                "valueKind": "object",
                "canonicalBytes": 209,
                "canonicalSha256": (
                    "97c9c2f8df1b4b66395aa3ac788e7013065aed12112894facc8"
                    "73a8f96cb7eca"
                ),
            },
            {
                "pointer": "/sourceInventory/runtime/fen",
                "field": "fen",
                "role": "runtime-board-parser-source-path",
                "valueKind": "string",
                "canonicalBytes": 111,
                "canonicalSha256": (
                    "b3851a35c54850c8b30dcd4b76f4f2d1927a6335a64875eecf"
                    "182b17ed9340c1"
                ),
            },
            {
                "pointer": "/sourceInventory/runtime/position",
                "field": "position",
                "role": "runtime-board-state-source-path",
                "valueKind": "string",
                "canonicalBytes": 111,
                "canonicalSha256": (
                    "a93adbc9bc8fd17ab1c2f0fa26d59ebe7c67a334fbce25592564"
                    "b312edd2d19a"
                ),
            },
        ),
    },
    {
        "path": str(
            REPO
            / "build-msvc"
            / "data-generation"
            / "deep-hce-v3"
            / "king-state-v1-prelabel.seal.json"
        ),
        "bytes": 34020,
        "sha256": (
            "7bcabd17743c8c42e87d700b0842d0fc07efb0e5d4e55b2bbc0"
            "81a4880769a92"
        ),
        "aliases": (
            {
                "pointer": "/identities/runtime/fen",
                "field": "fen",
                "role": "runtime-board-parser-source-identity",
                "valueKind": "object",
                "canonicalBytes": 210,
                "canonicalSha256": (
                    "bf86ca5b487a6f858e3a16452c30a12b77b7de21bf4efea80c"
                    "362ca16231ceb7"
                ),
            },
            {
                "pointer": "/identities/runtime/position",
                "field": "position",
                "role": "runtime-board-state-source-identity",
                "valueKind": "object",
                "canonicalBytes": 209,
                "canonicalSha256": (
                    "97c9c2f8df1b4b66395aa3ac788e7013065aed12112894facc8"
                    "73a8f96cb7eca"
                ),
            },
            {
                "pointer": "/sourceInventory/runtime/fen",
                "field": "fen",
                "role": "runtime-board-parser-source-path",
                "valueKind": "string",
                "canonicalBytes": 111,
                "canonicalSha256": (
                    "b3851a35c54850c8b30dcd4b76f4f2d1927a6335a64875eecf"
                    "182b17ed9340c1"
                ),
            },
            {
                "pointer": "/sourceInventory/runtime/position",
                "field": "position",
                "role": "runtime-board-state-source-path",
                "valueKind": "string",
                "canonicalBytes": 111,
                "canonicalSha256": (
                    "a93adbc9bc8fd17ab1c2f0fa26d59ebe7c67a334fbce25592564"
                    "b312edd2d19a"
                ),
            },
        ),
    },
    {
        "path": str(
            WORKSPACE
            / "omega-lab"
            / "schemas"
            / "search-regression-suite.schema.json"
        ),
        "bytes": 3990,
        "sha256": (
            "8be28f1ebbe63369448c0483f51dc8c12f651a54b92f723f061"
            "8409cd8914fb5"
        ),
        "aliases": (
            {
                "pointer": "/$defs/case/properties/ofen",
                "field": "ofen",
                "role": "suite-board-field-schema",
                "valueKind": "object",
                "canonicalBytes": 31,
                "canonicalSha256": (
                    "9ac136edb99a2063b3091602181c8d2022fdccb5b514991319d5"
                    "8544c2e57e95"
                ),
            },
        ),
    },
    {
        "path": str(
            WORKSPACE
            / "omega-lab"
            / "schemas"
            / "search-regression-report-v2.schema.json"
        ),
        "bytes": 9605,
        "sha256": (
            "d49c0790fd11c644328f25c9e98260e11c675b1f3672d30f1d3"
            "45989e5f61400"
        ),
        "aliases": (
            {
                "pointer": "/$defs/reference/properties/postOfen",
                "field": "postofen",
                "role": "report-optional-board-field-schema",
                "valueKind": "object",
                "canonicalBytes": 26,
                "canonicalSha256": (
                    "174e29b899b9ef9154a4d74f2c92ab3c556781ce17e916f91e1"
                    "4d80521818844"
                ),
            },
            {
                "pointer": "/$defs/result/properties/ofen",
                "field": "ofen",
                "role": "report-board-field-schema",
                "valueKind": "object",
                "canonicalBytes": 31,
                "canonicalSha256": (
                    "9ac136edb99a2063b3091602181c8d2022fdccb5b514991319d5"
                    "8544c2e57e95"
                ),
            },
        ),
    },
    {
        "path": str(
            WORKSPACE
            / "omega-lab"
            / "reports"
            / "senpai-combined-capabilities.json"
        ),
        "bytes": 494,
        "sha256": (
            "c787f14abaa9f5acc02d96220740e6dedf092dee30e973819dc0"
            "021b1aba21c3"
        ),
        "aliases": (
            {
                "pointer": "/checks/ofen",
                "field": "ofen",
                "role": "capability-board-check-result",
                "valueKind": "string",
                "canonicalBytes": 6,
                "canonicalSha256": (
                    "fedce8e8af485c2586f54c43a9b656dc70b41eb9be8c64b69c"
                    "98391847dd4baf"
                ),
            },
        ),
    },
    {
        "path": str(
            WORKSPACE
            / "omega-lab"
            / "reports"
            / "senpai-experiment-base-capabilities.json"
        ),
        "bytes": 494,
        "sha256": (
            "9ea240490b272f26884866b9864ab90a5dfba435f632808381c"
            "2ce30cffc0184"
        ),
        "aliases": (
            {
                "pointer": "/checks/ofen",
                "field": "ofen",
                "role": "capability-board-check-result",
                "valueKind": "string",
                "canonicalBytes": 6,
                "canonicalSha256": (
                    "fedce8e8af485c2586f54c43a9b656dc70b41eb9be8c64b69c"
                    "98391847dd4baf"
                ),
            },
        ),
    },
    {
        "path": str(
            WORKSPACE
            / "omega-lab"
            / "reports"
            / "senpai-opening-book-capabilities.json"
        ),
        "bytes": 494,
        "sha256": (
            "a79a74a9e45dd3f968b38acb70b99ff141fd3de86aa0f6d77f"
            "320cf42c1cfbda"
        ),
        "aliases": (
            {
                "pointer": "/checks/ofen",
                "field": "ofen",
                "role": "capability-board-check-result",
                "valueKind": "string",
                "canonicalBytes": 6,
                "canonicalSha256": (
                    "fedce8e8af485c2586f54c43a9b656dc70b41eb9be8c64b69c"
                    "98391847dd4baf"
                ),
            },
        ),
    },
    {
        "path": str(
            REPO
            / "validation"
            / "omega-nnue-king-state-v3-amendment-002.json"
        ),
        "bytes": 7192,
        "sha256": (
            "833886a638ebda8d062dfd195d22cfc482152730284d7da5f73a"
            "5066fb69c266"
        ),
        "aliases": (
            {
                "pointer": "/classifiedExistingEvidence/fen",
                "field": "fen",
                "role": "inventory-board-class-rationale",
                "valueKind": "string",
                "canonicalBytes": 70,
                "canonicalSha256": (
                    "40661da01c4746e09594aa3fdd33433d88fa7003f8546a92a3f"
                    "df27c63fec0cd"
                ),
            },
            {
                "pointer": (
                    "/classifiedExistingEvidence/canonicalrootofen"
                ),
                "field": "canonicalrootofen",
                "role": "inventory-board-class-rationale",
                "valueKind": "string",
                "canonicalBytes": 59,
                "canonicalSha256": (
                    "15f3e0b40f2d530f6321f2fc11a16f7fc2cc3a47167c453460b"
                    "ecbd91e6d0a21"
                ),
            },
            {
                "pointer": (
                    "/classifiedExistingEvidence/positionbefore"
                ),
                "field": "positionbefore",
                "role": "inventory-board-class-rationale",
                "valueKind": "string",
                "canonicalBytes": 26,
                "canonicalSha256": (
                    "b28a574c7a78f96fb7e89d6b5475a47563504124c6ed2f87e37"
                    "990d8a766c71a"
                ),
            },
            {
                "pointer": (
                    "/classifiedExistingEvidence/positionafter"
                ),
                "field": "positionafter",
                "role": "inventory-board-class-rationale",
                "valueKind": "string",
                "canonicalBytes": 31,
                "canonicalSha256": (
                    "cfa0739cc85731b5b9a4c550767d3fc288a89591b9bb044bac16"
                    "8c160d249c7d"
                ),
            },
            {
                "pointer": "/classifiedExistingEvidence/position",
                "field": "position",
                "role": "inventory-board-class-rationale",
                "valueKind": "string",
                "canonicalBytes": 25,
                "canonicalSha256": (
                    "7649d4de968f4207d5513df9a256f67e098dcf20813e335140db"
                    "910cf62b96c0"
                ),
            },
            {
                "pointer": (
                    "/classifiedExistingEvidence/stopposition"
                ),
                "field": "stopposition",
                "role": "inventory-board-class-rationale",
                "valueKind": "string",
                "canonicalBytes": 37,
                "canonicalSha256": (
                    "dbef6c7de9774579cae4ebc1187061f3571f092c8b869c09d1fe"
                    "3d0aa78dc5d0"
                ),
            },
            {
                "pointer": "/classifiedExistingEvidence/sourceofen",
                "field": "sourceofen",
                "role": "inventory-board-class-rationale",
                "valueKind": "string",
                "canonicalBytes": 34,
                "canonicalSha256": (
                    "0f48f456a82aaa7a0b75904ee28abdc5c083955ae704f26ac050"
                    "ac463be484bc"
                ),
            },
            {
                "pointer": "/classifiedExistingEvidence/positionkey",
                "field": "positionkey",
                "role": "inventory-board-class-rationale",
                "valueKind": "string",
                "canonicalBytes": 76,
                "canonicalSha256": (
                    "fbe5497e81dcb57432400e165eb1d50018a8db543073ae04b3d"
                    "5098c36fe45f1"
                ),
            },
            {
                "pointer": "/classifiedExistingEvidence/sourceofens",
                "field": "sourceofens",
                "role": "inventory-board-class-rationale",
                "valueKind": "string",
                "canonicalBytes": 52,
                "canonicalSha256": (
                    "a8d3d41a97c885fb1a927be07e693af22387e46b721e7cdb7f5"
                    "f6841c2c30ac9"
                ),
            },
            {
                "pointer": "/classifiedExistingEvidence/positions",
                "field": "positions",
                "role": "inventory-board-class-rationale",
                "valueKind": "string",
                "canonicalBytes": 40,
                "canonicalSha256": (
                    "d97f4591cca6134d60ccd813ef39ed20633d613682eb31c6ede6"
                    "ab7f3a26fd6d"
                ),
            },
        ),
    },
)
EXPECTED_STRUCTURAL_SCALAR_ALIAS_COUNTS = {
    "capability-board-check-result": 3,
    "inventory-board-class-rationale": 10,
    "report-board-field-schema": 1,
    "report-optional-board-field-schema": 1,
    "runtime-board-parser-source-identity": 2,
    "runtime-board-parser-source-path": 2,
    "runtime-board-state-source-identity": 2,
    "runtime-board-state-source-path": 2,
    "suite-board-field-schema": 1,
}
STRUCTURAL_SCALAR_ALIAS_SOURCE_BY_PATH = MappingProxyType(
    {
        os.path.normcase(str(Path(str(source["path"])).resolve())): source
        for source in STRUCTURAL_SCALAR_ALIAS_SOURCES
    }
)
OPENING_REPLAY_REQUEST_KIND = (
    "omega-opening-prefix-replay-request-v1"
)
OPENING_REPLAY_PREFIX_KIND = (
    "omega-opening-prefix-replay-position-v1"
)
OPENING_REPLAY_MANIFEST_KIND = (
    "omega-opening-prefix-replay-manifest-v1"
)
OPENING_REPLAY_CORECHESS_HEAD_COMMIT = (
    "4da81166b7081f33c4492a1ebc404bd2e542c325"
)
OPENING_REPLAY_PINS = {
    "project": {
        "path": str(OPENING_REPLAY_PROJECT),
        "bytes": 355,
        "sha256": (
            "518c270c9a69d73551870442755222dcdd62c6b6e5f148b65"
            "c128284c8088c29"
        ),
    },
    "source": {
        "path": str(OPENING_REPLAY_SOURCE),
        "bytes": 35802,
        "sha256": (
            "1d340f2dd2699531889b9b1d28fc50e062f6eace6b85d53951"
            "b07591de8e60f8"
        ),
    },
    "helper": {
        "path": str(OPENING_REPLAY_HELPER),
        "bytes": 54272,
        "sha256": (
            "a7407261529df4908458905c3d32f46a461d76625b77e6d23ec"
            "d6f9f73e099e3"
        ),
    },
    "rulesAssembly": {
        "path": str(OPENING_REPLAY_RULES),
        "bytes": 248832,
        "sha256": (
            "f4e3e5f04590e6811279f31e226025694b1ba332689ec915341"
            "aeae9a2f46438"
        ),
    },
    "dependencyManifest": {
        "path": str(OPENING_REPLAY_DEPS),
        "bytes": 16124,
        "sha256": (
            "49ff3bac5aaa6bbcb528ee49c28bb235bad32abd7dda8e39fcf"
            "e466520661e13"
        ),
    },
    "runtimeConfig": {
        "path": str(OPENING_REPLAY_RUNTIMECONFIG),
        "bytes": 342,
        "sha256": (
            "c230a317a54dd960bcbeb5f347f52e18dc665a26f7efda2159"
            "fced9a5ac7e097"
        ),
    },
    "rulesSource": {
        "path": str(OPENING_REPLAY_RULES_SOURCE),
        "bytes": 139837,
        "sha256": (
            "01fcf139db44acc28ad8c34e79424317ea0b6d3a11e2dce512"
            "1bdb7b7f92b007"
        ),
    },
    "rulesProject": {
        "path": str(OPENING_REPLAY_RULES_PROJECT),
        "bytes": 359,
        "sha256": (
            "881ac4f4ff1e5712aaa34a0dc04a1111943047533a56ca77a9e"
            "7aeb1f3344320"
        ),
    },
    "dotnetHost": {
        "path": str(DOTNET),
        "bytes": 167248,
        "sha256": (
            "a5ccdc3a41d5e5c6014ff64509aed176db39f4f14caffff3dd"
            "1997f8907e94d7"
        ),
    },
}
OPENING_REPLAY_RUNTIME_BUNDLE_PIN = {
    "path": str(OPENING_REPLAY_RUNTIME),
    "assemblyRelativePath": "OmegaOpeningPrefixReplay.dll",
    "fileCount": 26,
    "bytes": 1_861_099,
    "sha256": (
        "9eb3d7f23ab6fd944a0254fd07cf8021fb4d03511e26ed32bfbe"
        "702a41bd6218"
    ),
}
ROOT_SAMPLER_PINS = {
    "project": {
        "path": str(SAMPLER_PROJECT),
        "bytes": 355,
        "sha256": (
            "518c270c9a69d73551870442755222dcdd62c6b6e5f148b65c"
            "128284c8088c29"
        ),
    },
    "source": {
        "path": str(SAMPLER_SOURCE),
        "bytes": 13271,
        "sha256": (
            "289d2452fdb63c2737c72b4b3634e8d1b2023126346f3fb1a6"
            "62f752e64df3d2"
        ),
    },
    "assembly": {
        "path": str(ROOT_SAMPLER_ASSEMBLY),
        "bytes": 44544,
        "sha256": (
            "f443506d478ff1696558e6a23064e44d20a29b7a4a855221af6"
            "a18c57b395b53"
        ),
    },
    "rulesAssembly": {
        "path": str(ROOT_SAMPLER_RUNTIME / "ChessLib.dll"),
        "bytes": 248832,
        "sha256": (
            "f4e3e5f04590e6811279f31e226025694b1ba332689ec915341"
            "aeae9a2f46438"
        ),
    },
    "rulesSource": {
        "path": str(OPENING_REPLAY_RULES_SOURCE),
        "bytes": 139837,
        "sha256": (
            "01fcf139db44acc28ad8c34e79424317ea0b6d3a11e2dce512"
            "1bdb7b7f92b007"
        ),
    },
    "rulesProject": {
        "path": str(OPENING_REPLAY_RULES_PROJECT),
        "bytes": 359,
        "sha256": (
            "881ac4f4ff1e5712aaa34a0dc04a1111943047533a56ca77a9e"
            "7aeb1f3344320"
        ),
    },
    "appLocalRuntimeBundle": {
        "path": str(ROOT_SAMPLER_RUNTIME),
        "assemblyRelativePath": "OmegaRootSampler.dll",
        "fileCount": 26,
        "bytes": 1_850_835,
        "sha256": (
            "6f1c8ea0c2dd41430ef61d7b1ee4a99dd4449d52816a9a2245"
            "03a6072c2e6ff4"
        ),
    },
}
HISTORY_SNAPSHOT_PINS = {
    "project": {
        "path": str(HISTORY_SNAPSHOT_PROJECT),
        "bytes": 355,
        "sha256": (
            "518c270c9a69d73551870442755222dcdd62c6b6e5f148b65c"
            "128284c8088c29"
        ),
    },
    "source": {
        "path": str(HISTORY_SNAPSHOT_SOURCE),
        "bytes": 15859,
        "sha256": (
            "267169d446cf4fcdcd26ad12644023a0ef7766f203b973d8290"
            "166b0dc50236a"
        ),
    },
    "assembly": {
        "path": str(HISTORY_SNAPSHOT_ASSEMBLY),
        "bytes": 36352,
        "sha256": (
            "4c66833c12debdcc4dacb9e92fffab5b979eff7a3ed5ed09cda"
            "ebabfeff2a5fb"
        ),
    },
    "rulesAssembly": {
        "path": str(HISTORY_SNAPSHOT_RUNTIME / "ChessLib.dll"),
        "bytes": 248832,
        "sha256": (
            "f4e3e5f04590e6811279f31e226025694b1ba332689ec915341"
            "aeae9a2f46438"
        ),
    },
    "rulesSource": {
        "path": str(OPENING_REPLAY_RULES_SOURCE),
        "bytes": 139837,
        "sha256": (
            "01fcf139db44acc28ad8c34e79424317ea0b6d3a11e2dce512"
            "1bdb7b7f92b007"
        ),
    },
    "rulesProject": {
        "path": str(OPENING_REPLAY_RULES_PROJECT),
        "bytes": 359,
        "sha256": (
            "881ac4f4ff1e5712aaa34a0dc04a1111943047533a56ca77a9e"
            "7aeb1f3344320"
        ),
    },
    "appLocalRuntimeBundle": {
        "path": str(HISTORY_SNAPSHOT_RUNTIME),
        "assemblyRelativePath": "OmegaHistorySnapshot.dll",
        "fileCount": 26,
        "bytes": 1_843_167,
        "sha256": (
            "432c476dc56dfe71e31af10fe6426f5e677b061cc9dc6a5819f"
            "3adf7a1575c9f"
        ),
    },
}
OMEGA_MATCH_PINS = {
    "assembly": {
        "path": str(HARNESS),
        "bytes": 176640,
        "sha256": (
            "2f66adb65aaa9c1075ee27374bf8f02e95d09a1108f674cc332"
            "a6d9f7cc10e70"
        ),
    },
    "appLocalRuntimeBundle": {
        "path": str(HARNESS.parent),
        "assemblyRelativePath": "OmegaMatch.dll",
        "fileCount": 25,
        "bytes": 2_004_808,
        "sha256": (
            "36dcaea0a528f30316e54cb020971cd5d3328390b41d90325840"
            "aae55fe3d3dd"
        ),
    },
}
DOTNET_RUNTIME_PINS = {
    "host": OPENING_REPLAY_PINS["dotnetHost"],
    "hostFxrSelectionNamespace": {
        "path": str(DOTNET_HOSTFXR_SELECTION_DIR),
        "versionDirectories": ["10.0.9"],
        "fileCount": 1,
        "bytes": 379728,
        "sha256": (
            "afb0fcbb37547b54c2cd3cbebdcd21d209e2aac55e277724e22f"
            "b5ea29a451dd"
        ),
    },
    "hostFxrBundle": {
        "path": str(DOTNET_HOSTFXR_DIR),
        "fileCount": 1,
        "bytes": 379728,
        "sha256": (
            "e4cbb55fe78707b6b2a6bf3299a8f11ebdca5ed397e7a2cefa"
            "1769561752eef8"
        ),
    },
    "sharedFrameworkSelectionNamespace": {
        "path": str(DOTNET_SHARED_RUNTIME_SELECTION_DIR),
        "versionDirectories": ["10.0.9"],
        "fileCount": 189,
        "bytes": 79_709_188,
        "sha256": (
            "f4aa7e85c288786345f40ce7568dcdfec46bbdf940746ed7fc8b"
            "8ad5bf85a09c"
        ),
    },
    "sharedFrameworkBundle": {
        "path": str(DOTNET_SHARED_RUNTIME_DIR),
        "fileCount": 189,
        "bytes": 79_709_188,
        "sha256": (
            "2d9faf280d5438d320c7070132bdfc711934d91ebfcb412f185d"
            "4a97443c3cfb"
        ),
    },
}
DOTNET_ENVIRONMENT_POLICY = {
    "clearedAmbientPrefixesCaseInsensitive": [
        "COMPLUS_",
        "COR_",
        "CORE_",
        "CORECLR_",
        "COREHOST_",
        "DOTNET_",
    ],
    "clearedAmbientNamesCaseInsensitive": [
        "ProgramFiles(x86)",
    ],
    "setVariables": {
        "DOTNET_EnableDiagnostics": "0",
        "DOTNET_MULTILEVEL_LOOKUP": "0",
        "DOTNET_ROLL_FORWARD": "LatestPatch",
        "DOTNET_ROLL_FORWARD_TO_PRERELEASE": "0",
        "DOTNET_ROOT": str(DOTNET.parent),
    },
    "inheritedAmbientRedirectorsForbidden": True,
    "runtimePinsVerifiedImmediatelyBeforeEveryManagedInvocation": True,
}

PREREG_PIN = {
    "path": str(PREREGISTRATION),
    "bytes": 33071,
    "sha256": "7ed714f11e370bb183815bd597a58cce5090396d1b6fe1b0a07849994b48c10c",
}
AMENDMENT_PIN = {
    "path": str(AMENDMENT),
    "bytes": 8729,
    "sha256": "846f43487558246edec871359f9f0a5dbd0377e69a3b95baee6ab6f47f32f78b",
}
AMENDMENT_002_PIN = {
    "path": str(AMENDMENT_002),
    "bytes": 7192,
    "sha256": "833886a638ebda8d062dfd195d22cfc482152730284d7da5f73a5066fb69c266",
}
AMENDMENT_003_PIN = {
    "bytes": 68249,
    "path": str(AMENDMENT_003),
    "sha256": "30d4eeb080d7a8d07d20b11abe8f3b2df19dc8c5e3c4ab354c523fcdce642469",
}
INCIDENT_PIN = {
    "path": str(GEN2_INCIDENT),
    "bytes": 22747,
    "sha256": "df4b440ec625e3beb33735f3e89908c1414208f11d42a60944f8089075821678",
}
PHASE_IMPLEMENTATION_PIN = {
    "path": str(Path(incidence.__file__).resolve()),
    "bytes": 31219,
    "sha256": "d58f1cb9150c79910c460dea7e95aefd2153e9ce3c8866cd2da3f5a953d591b9",
}
STATIC_HCE_SHA256 = (
    "b6bdd2310901319d85464a1cb570557dfceaee0ebfefba0"
    "563c9564728c27c04"
)
TEACHER_ENGINE_PIN = {
    "path": str(ENGINE),
    "bytes": 619520,
    "sha256": "37b4846f6f12cd795730d6472279007a0f53d8653e2b9a0071c1d13d2eed09e0",
}

K2_NETWORK = REPO / "build-msvc" / "king-state-v2" / "K2.nnue"
K2_MANIFEST = REPO / "build-msvc" / "king-state-v2" / "K2.manifest.json"
K0_NETWORK = REPO / "build-msvc" / "king-state-v2" / "K0.nnue"
K0_MANIFEST = REPO / "build-msvc" / "king-state-v2" / "K0.manifest.json"
GEN2_SELECTION = (
    REPO / "build-msvc" / "king-state-v2"
    / "validation-selection.seal.json"
)
GEN2_READINESS = (
    REPO / "build-king-state-v2" / "readiness"
    / "king-state-v2-match-readiness.seal.json"
)
K2_EXPECTED = {
    "network": {
        "path": str(K2_NETWORK),
        "bytes": 12392940,
        "sha256": "74cfefdad87ce4e620597f1153bf03aa88b301cf66b2c959700b07e85acafe33",
    },
    "manifest": {
        "path": str(K2_MANIFEST),
        "bytes": 116217,
        "sha256": "48e0666d6a86bc253d435ee0b42908667dfb200d3cfc27bcbef9dbeced677b99",
    },
    "selection": {
        "path": str(GEN2_SELECTION),
        "bytes": 15015,
        "sha256": "44224d2fa3db382d0045615a8aa758b45b654e45c2374dbe9321e042fed1bd3f",
    },
    "readiness": {
        "path": str(GEN2_READINESS),
        "bytes": 1515733,
        "sha256": "b82f3c0f43a15b3f090fb61435005cd3ba43c8a6bcb895a559a2bc5ac2826f50",
    },
}
K0_EXPECTED = {
    "network": {
        "path": str(K0_NETWORK),
        "bytes": 12392940,
        "sha256": "3aeb0a879a04c475f7efa786709b5643685843f739d9c241cca8ff3567306c4d",
    },
    "manifest": {
        "path": str(K0_MANIFEST),
        "bytes": 22789,
        "sha256": "17394e7aa3a448aa88cb4b6ff21ed25e1378003cc1108f9aaabd5125deed9a50",
    },
}

AMENDMENT_002_OFEN_FIELDS = frozenset(
    (
        "ofen",
        "fen",
        "preofen",
        "postofen",
        "initialofen",
        "finalofen",
        "canonicalrootofen",
        "position",
        "positionbefore",
        "positionafter",
        "stopposition",
        "sourceofen",
        "positionkey",
    )
)
AMENDMENT_003_ADDITIONAL_OFEN_FIELDS = frozenset(
    ("officialinitialofen",)
)
OFEN_FIELDS = (
    AMENDMENT_002_OFEN_FIELDS | AMENDMENT_003_ADDITIONAL_OFEN_FIELDS
)
PARTIAL_OFEN_FIELDS = frozenset(("positionkey",))
PARTIAL_OFEN_CANONICAL_SUFFIX = ("0", "1")
POSITION_CONTAINER_FIELDS = frozenset(("positions", "sourceofens"))
AMENDMENT_002_OPAQUE_POSITION_METADATA_FIELDS = frozenset(
    (
        "additionalexcludedpositionsread",
        "candidatepositions",
        "candidatepositionsbeforeexclusion",
        "candidatepositionsconsidered",
        "caseinsensitivescalarofenfields",
        "clear_hash_each_position",
        "conflictpositioncount",
        "continuationpositioncount",
        "directarraystringleavesdecodedasofencandidates",
        "disposition",
        "eligiblecandidatepositions",
        "endgamepositions",
        "excludedpositionartifacts",
        "excludeeverygeneration1andgeneration2heldoutposition",
        "excludeeverygeneration1andgeneration2trainingposition",
        "excludeeverygeneration1andgeneration2validationposition",
        "excludeeverypositionbearingartifactdiscoverablebeforefreeze",
        "excludeeverypriornnuematchposition",
        "excludeeverypriornnuescreenandconfirmationposition",
        "featurerowsmaybederivedfromofen",
        "fenheader",
        "finalpositioncount",
        "finalpositionsaccepted",
        "forbiddenpositionsread",
        "freshprocessperposition",
        "frozenregressionpositionsread",
        "invalidofencandidatecontributesnoexclusionsignature",
        "invalidofensignored",
        "labeledtrainingpositions",
        "largesttranspositioncomponent",
        "matchingordinarystringabortsasunclassifiedpotentialposition",
        "maxmovesperposition",
        "nestedarraystringleavesdecodedasofencandidates",
        "nodes_per_position",
        "normalizedofenexact",
        "ofensequencesha256",
        "ofensha256",
        "orbitdisjointfromeverygeneration1generation2andgeneration3trainingvalidationheldoutandpriormatchposition",
        "outputpositions",
        "phasederivedfromofen",
        "phasederivedfromofenpiececount",
        "position_index",
        "positioncount",
        "positionheader",
        "positionhitpercent",
        "positionhits",
        "positionlikekeycompleteness",
        "positionrank",
        "positionrole",
        "positions_scanned",
        "positionsaccepted",
        "positionschecked",
        "positionsperphaseandside",
        "positionsperphaseside",
        "positionsprobed",
        "positionsrejected",
        "priorsuitecanonicalofensexcluded",
        "priorsuiteofensandreflectionsexcluded",
        "quieteligiblepositionsbeforededuplication",
        "recognizedofenkeys",
        "recognizedomegaofenshape",
        "rootreachablepositionpercent",
        "rootreachablepositions",
        "scientificdisposition",
        "searchdepthperposition",
        "senpaiofenacceptance",
        "sidetomovemaybederivedfromofen",
        "sourcecandidatepositions",
        "sourceofencount",
        "trainingpositions",
        "transpositioncomponents",
        "transpositionmerges",
    )
)
AMENDMENT_003_OPAQUE_POSITION_METADATA_FIELDS = frozenset(
    (
        "active-match-position",
        "additionalscalarofenfields",
        "eligiblepositionsbeforeboardorbitdeduplication",
        "eligiblepositionsbeforeorbitdeduplication",
        "historicalpositionroots",
        "invalid-ofen-or-ply",
        "position.castling-rights",
        "position.champion-imbalance-not-one",
        "position.current-check",
        "position.en-passant-state",
        "position.halfmove-edge",
        "position.immediate-capture",
        "position.immediate-legal-capture",
        "position.immediate-legal-promotion",
        "position.immediate-promotion",
        "position.king-in-check",
        "position.no-matched-cross-identity-pair",
        "position.no-multitype-leaper-capture",
        "position.no-safe-champion-activation",
        "position.no-safe-wizard-activation",
        "position.no-same-champion-safe-option-gap-pair",
        "position.no-unmoved-original-champion",
        "position.no-unmoved-original-wizard",
        "position.one-or-fewer-legal-moves",
        "position.original-wizard-missing-or-mutated",
        "position.original-wizard-no-eligible-landing",
        "position.pawn-count-outside-p01-20",
        "position.phase-outside-u01-24",
        "position.prior-selected-board-orbit",
        "position.recorded-next-move-is-check",
    )
)
OPAQUE_POSITION_METADATA_FIELDS = (
    AMENDMENT_002_OPAQUE_POSITION_METADATA_FIELDS
    | AMENDMENT_003_OPAQUE_POSITION_METADATA_FIELDS
)
POSITION_LIKE_KEY_FRAGMENTS = ("ofen", "fen", "position")
JSON_TOKEN = re.compile(
    r'[ \t\r\n]*(?:(?P<string>"(?:[^"\\\x00-\x1f]|'
    r'\\(?:["\\/bfnrt]|u[0-9a-fA-F]{4}))*")'
    r'|(?P<number>-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?'
    r'(?:[eE][+-]?[0-9]+)?)'
    r'|(?P<literal>true|false|null)'
    r'|(?P<punctuation>[{}\[\]:,]))'
)
JSON_WHITESPACE_TO_END = re.compile(r"[ \t\r\n]*\Z")
RAW_JSON_STRING_TOKEN = re.compile(
    r'"(?:[^"\\\x00-\x1f]|\\(?:["\\/bfnrt]|u[0-9a-fA-F]{4}))*"'
)
COMPLEMENT_SENTINEL_KEYS = frozenset(
    (
        "initialofen",
        "moves",
        "openingmoves",
        "openings",
        "positions",
        "sourceofens",
    )
)
COMPLEMENT_ALLOWED_HIT_CLASSES = {
    "840faa76475e8297eb73eb33805fabf98202a5002143bb19570f9ec5aec95955": {
        "class": "generated-canonical-public-opening-copy",
        "bytes": 2996,
        "keys": {"moves": 24, "openings": 1},
        "valueTypes": {"moves:array": 24, "openings:array": 1},
        "rawOfenShapeHits": 0,
        "normalizedLfSha256": (
            "840faa76475e8297eb73eb33805fabf98202a5002143bb19570f"
            "9ec5aec95955"
        ),
    },
    "ddc05c7ca98e55e07539d8ca693ae420448e887aa847a93b44e30474ec62a7c5": {
        "class": "generated-style-public-opening-copy",
        "bytes": 3027,
        "keys": {"moves": 24, "openings": 1},
        "valueTypes": {"moves:array": 24, "openings:array": 1},
        "rawOfenShapeHits": 0,
        "normalizedLfSha256": (
            "840faa76475e8297eb73eb33805fabf98202a5002143bb19570f"
            "9ec5aec95955"
        ),
    },
    "51dfc67c6abd917342bd07cc1e7ce2928a4660315b62cf3b8194e12854aba939": {
        "class": "generated-omega-rules-fixture-copy",
        "bytes": 647,
        "keys": {"positions": 1},
        "valueTypes": {"positions:array": 1},
        "rawOfenShapeHits": 3,
    },
    "5aca44116a1f6f5baf7c47480108014ec10feaebdd67e4ce4bb6951f57b0e05d": {
        "class": "translation-en-core",
        "bytes": 7490,
        "keys": {"moves": 1},
        "valueTypes": {"moves:string": 1},
        "rawOfenShapeHits": 0,
    },
    "6b66859729c050fd3a17aa63b0a0c26fd4235405f3049dab6698ad18bff2af30": {
        "class": "translation-it-core",
        "bytes": 8067,
        "keys": {"moves": 1},
        "valueTypes": {"moves:string": 1},
        "rawOfenShapeHits": 0,
    },
    "49951ba4846fc73875f3fbbf5aa0e35a03f6e619d7927a15b405baafd08f483f": {
        "class": "translation-en-omega",
        "bytes": 8468,
        "keys": {"moves": 1},
        "valueTypes": {"moves:string": 1},
        "rawOfenShapeHits": 0,
    },
    "c3f263be56e9cb59fe2a1ceef689bafe75ff604c417f10d7f93e6852306bb457": {
        "class": "translation-it-omega",
        "bytes": 9134,
        "keys": {"moves": 1},
        "valueTypes": {"moves:string": 1},
        "rawOfenShapeHits": 0,
    },
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _resolve(path: Path) -> Path:
    return Path(path).expanduser().resolve()


def _generation3_destination_for(path: Path) -> Path | None:
    """Return the exact fresh-output subtree containing ``path``.

    These locations are prospective generation-3 products, never
    historical evidence for the generation that creates them.  The check is
    structural and therefore remains effective before a destination exists.
    """

    resolved = _resolve(path)
    for supplied in GENERATION3_DESTINATION_SUBTREES:
        destination = _resolve(supplied)
        if resolved == destination or destination in resolved.parents:
            return destination
    return None


def _generation3_destination_contract() -> list[dict[str, str]]:
    return [
        {
            "destinationId": destination_id,
            "pathBase": base,
            "relativePath": relative_path,
        }
        for destination_id, base, relative_path, _path
        in GENERATION3_DESTINATION_DECLARATIONS
    ]


FRESH_SOURCE_ARTIFACTS = frozenset(
    _resolve(path)
    for path in (RULES_ROOTS, RULES_MANIFEST, RULES_FREEZE)
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with _resolve(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _identity(path: Path) -> dict[str, Any]:
    resolved = _resolve(path)
    stat = resolved.stat()
    return {
        "path": str(resolved),
        "bytes": stat.st_size,
        "sha256": _sha256(resolved),
    }


def _same_identity(left: Any, right: Any) -> bool:
    if not isinstance(left, Mapping) or not isinstance(right, Mapping):
        return False
    try:
        return (
            _resolve(Path(str(left["path"])))
            == _resolve(Path(str(right["path"])))
            and int(left["bytes"]) == int(right["bytes"])
            and str(left["sha256"]).lower()
            == str(right["sha256"]).lower()
        )
    except (KeyError, TypeError, ValueError):
        return False


def _verify_identity(value: Mapping[str, Any], label: str) -> Path:
    try:
        current = _identity(Path(str(value["path"])))
    except (KeyError, FileNotFoundError) as error:
        raise ValueError(f"{label} identity is unavailable") from error
    if not _same_identity(value, current):
        raise ValueError(f"{label} identity changed: {current['path']}")
    return Path(current["path"])


def _canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _exclusive_bytes(path: Path, payload: bytes) -> None:
    target = _resolve(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(
        target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _exclusive_json(path: Path, value: Any) -> None:
    _exclusive_bytes(path, _canonical_bytes(value))


def _load_json(path: Path, label: str) -> dict[str, Any]:
    resolved = _resolve(path)
    before = _identity(resolved)
    try:
        value = json.loads(resolved.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"{label} is invalid JSON: {error}") from error
    if before != _identity(resolved):
        raise ValueError(f"{label} changed while read")
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _load_json_strict(path: Path, label: str) -> dict[str, Any]:
    resolved = _resolve(path)
    before = _identity(resolved)
    try:
        value = json.loads(
            resolved.read_text(encoding="utf-8"),
            object_pairs_hook=_strict_object_pairs,
        )
    except (json.JSONDecodeError, ValueError) as error:
        raise ValueError(f"{label} is invalid strict JSON: {error}") from error
    if before != _identity(resolved):
        raise ValueError(f"{label} changed while read")
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _expect(actual: Any, expected: Any, label: str) -> None:
    if actual != expected:
        raise ValueError(f"{label}: expected {expected!r}, got {actual!r}")


DRAFT_AMENDMENT_003_STATUS = (
    "draft target-free inventory and replay correction; not frozen and "
    "unusable for sampling or teacher labels"
)
FROZEN_AMENDMENT_003_STATUS = (
    "target-free inventory correction frozen before generation-3 "
    "sampling and teacher labels"
)
STRICT_UTC_RE = re.compile(
    r"\A\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"
    r"(?:\.\d{1,6})?Z\Z"
)


def _validate_strict_utc(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or STRICT_UTC_RE.fullmatch(value) is None:
        raise ValueError(
            f"{label} must be an ISO-8601 UTC timestamp ending in Z"
        )
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise ValueError(f"{label} is not a real UTC timestamp") from error
    if parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError(f"{label} is not UTC")
    return value


def _relative_identity(value: Mapping[str, Any]) -> dict[str, Any]:
    try:
        path = _resolve(Path(str(value["path"])))
        relative = path.relative_to(REPO).as_posix()
        byte_count = int(value["bytes"])
        sha256 = str(value["sha256"]).lower()
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("declaration identity is malformed") from error
    if byte_count < 0 or re.fullmatch(r"[0-9a-f]{64}", sha256) is None:
        raise ValueError("declaration identity is malformed")
    return {
        "path": relative,
        "bytes": byte_count,
        "sha256": sha256,
    }


def _document_identity(path: Path, value: Any) -> dict[str, Any]:
    identity = _content_identity(_canonical_bytes(value))
    return {
        "path": str(_resolve(path)),
        "bytes": identity["bytes"],
        "sha256": identity["sha256"],
    }


def _require_five_digit_amendment_size(value: Mapping[str, Any]) -> None:
    try:
        byte_count = int(value["bytes"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("amendment-003 byte count is malformed") from error
    if byte_count < 10000 or byte_count > 99999:
        raise ValueError(
            "amendment-003 must remain inside the exact five-digit "
            "byte-count envelope"
        )


def _validate_match_declaration_transaction(
    amendment_identity: Mapping[str, Any],
    *,
    protocol: Mapping[str, Any] | None = None,
    adapter: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    protocol_value = (
        dict(protocol)
        if protocol is not None
        else _load_json_strict(
            MATCH_PROTOCOL, "generation-3 match protocol"
        )
    )
    adapter_value = (
        dict(adapter)
        if adapter is not None
        else _load_json_strict(
            MATCH_ADAPTER, "generation-3 match adapter"
        )
    )
    expected_chain = [
        _relative_identity(AMENDMENT_PIN),
        _relative_identity(AMENDMENT_002_PIN),
        _relative_identity(amendment_identity),
    ]
    protocol_chain = protocol_value.get("amendmentChain")
    adapter_declarations = adapter_value.get("declarations")
    adapter_chain = (
        adapter_declarations.get("amendmentChain")
        if isinstance(adapter_declarations, dict)
        else None
    )
    _expect(
        protocol_chain,
        expected_chain,
        "match protocol exact ordered amendment chain",
    )
    _expect(
        adapter_chain,
        expected_chain,
        "match adapter exact ordered amendment chain",
    )
    compatibility = adapter_value.get("compatibility")
    if not isinstance(compatibility, dict):
        raise ValueError("match adapter compatibility declaration is absent")
    _expect(
        compatibility.get("protocol"),
        MATCH_PROTOCOL.relative_to(REPO).as_posix(),
        "match adapter canonical protocol path",
    )
    return protocol_value, adapter_value


ProgressCallback = Callable[[str, Mapping[str, Any]], None]


def _emit_progress(
    progress: ProgressCallback | None,
    stage: str,
    **values: Any,
) -> None:
    if progress is not None:
        progress(stage, values)


def _stderr_progress(stage: str, values: Mapping[str, Any]) -> None:
    payload = {
        "stage": stage,
        **{key: values[key] for key in sorted(values)},
    }
    print(
        "TARGET_FREE_PROGRESS="
        + json.dumps(payload, sort_keys=True, separators=(",", ":")),
        file=sys.stderr,
        flush=True,
    )


def _structural_scalar_alias_lookup(
    source_identity: Mapping[str, Any] | None,
) -> Mapping[tuple[str, str], Mapping[str, Any]]:
    if source_identity is None:
        return MappingProxyType({})
    try:
        source_path = os.path.normcase(
            str(_resolve(Path(str(source_identity["path"]))))
        )
    except (KeyError, TypeError, ValueError):
        raise ValueError("structural alias source identity is malformed")
    source = STRUCTURAL_SCALAR_ALIAS_SOURCE_BY_PATH.get(source_path)
    if source is None:
        return MappingProxyType({})
    if not _same_identity(source_identity, source):
        raise ValueError(
            "pinned structural scalar alias source identity changed"
        )
    return MappingProxyType(
        {
            (str(alias["pointer"]), str(alias["field"])): alias
            for alias in source["aliases"]
        }
    )


def _validate_structural_scalar_alias(
    text: str,
    start: int,
    end: int,
    *,
    alias: Mapping[str, Any],
    location: str,
) -> None:
    try:
        value = json.loads(
            text[start:end], object_pairs_hook=_strict_object_pairs
        )
    except (json.JSONDecodeError, ValueError) as error:
        raise ValueError(
            f"{location}: pinned structural scalar alias is malformed"
        ) from error
    expected_type = {
        "object": dict,
        "string": str,
    }.get(str(alias.get("valueKind")))
    if expected_type is None or not isinstance(value, expected_type):
        raise ValueError(
            f"{location}: pinned structural scalar alias has the wrong "
            "value kind"
        )
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    identity = _content_identity(payload)
    if (
        identity["bytes"] != alias["canonicalBytes"]
        or identity["sha256"] != alias["canonicalSha256"]
    ):
        raise ValueError(
            f"{location}: pinned structural scalar alias shape changed"
        )


def _expected_structural_alias_counts(
    files: Sequence[Path],
) -> Counter[str]:
    resolved = frozenset(_resolve(path) for path in files)
    expected: Counter[str] = Counter()
    for source in STRUCTURAL_SCALAR_ALIAS_SOURCES:
        if _resolve(Path(str(source["path"]))) not in resolved:
            continue
        expected.update(alias["role"] for alias in source["aliases"])
    return expected


def _validate_declarations(
    *, mode: str = "frozen"
) -> tuple[dict[str, Any], dict[str, Any]]:
    if mode not in {"draft", "frozen"}:
        raise ValueError(f"unknown amendment-003 validation mode: {mode!r}")
    _expect(
        _resolve(Path(str(AMENDMENT_003_PIN["path"]))),
        _resolve(generation3_trainer.AMENDMENT_003),
        "prelabel/trainer amendment-003 path",
    )
    _expect(
        int(AMENDMENT_003_PIN["bytes"]),
        generation3_trainer.AMENDMENT_003_BYTES,
        "prelabel/trainer amendment-003 bytes",
    )
    _expect(
        str(AMENDMENT_003_PIN["sha256"]).lower(),
        generation3_trainer.AMENDMENT_003_SHA256.lower(),
        "prelabel/trainer amendment-003 sha256",
    )
    _verify_identity(PREREG_PIN, "generation-3 preregistration")
    _verify_identity(AMENDMENT_PIN, "generation-3 amendment")
    _verify_identity(
        AMENDMENT_002_PIN, "generation-3 inventory amendment"
    )
    if mode == "frozen":
        _verify_identity(
            AMENDMENT_003_PIN, "generation-3 inventory correction"
        )
    _verify_identity(INCIDENT_PIN, "generation-2 incident")
    _verify_identity(
        PHASE_IMPLEMENTATION_PIN, "phase-incidence implementation"
    )
    profile = _load_json(PREREGISTRATION, "generation-3 preregistration")
    amendment = _load_json(AMENDMENT, "generation-3 amendment")
    amendment_002 = _load_json(
        AMENDMENT_002, "generation-3 inventory amendment"
    )
    amendment_003 = _load_json_strict(
        AMENDMENT_003, "generation-3 inventory correction"
    )
    _expect(profile.get("profileId"), PROFILE_ID, "profile id")
    _expect(amendment.get("profileId"), PROFILE_ID, "amendment profile id")
    _expect(
        amendment_002.get("profileId"),
        PROFILE_ID,
        "inventory amendment profile id",
    )
    _expect(
        amendment_002.get("amendmentId"),
        "king-state-v3-amendment-002",
        "inventory amendment id",
    )
    _expect(
        amendment_003.get("profileId"),
        PROFILE_ID,
        "inventory correction profile id",
    )
    _expect(
        amendment_003.get("amendmentId"),
        "king-state-v3-amendment-003",
        "inventory correction id",
    )
    expected_status = (
        FROZEN_AMENDMENT_003_STATUS
        if mode == "frozen"
        else DRAFT_AMENDMENT_003_STATUS
    )
    _expect(
        amendment_003.get("status"),
        expected_status,
        f"inventory correction {mode} status",
    )
    if mode == "frozen":
        _validate_strict_utc(
            amendment_003.get("createdUtc"),
            label="inventory correction createdUtc",
        )
    else:
        _expect(
            amendment_003.get("createdUtc"),
            None,
            "draft inventory correction createdUtc",
        )
    _expect(
        amendment_002.get("status"),
        (
            "target-free inventory field clarification frozen before "
            "generation-3 sampling and teacher labels"
        ),
        "inventory amendment frozen status",
    )
    if not isinstance(amendment_002.get("createdUtc"), str):
        raise ValueError("inventory amendment has no frozen timestamp")
    _expect(
        amendment_002.get("pinnedPriorAmendment"),
        {
            "path": (
                "validation/"
                "omega-nnue-king-state-v3-amendment-001.json"
            ),
            "bytes": AMENDMENT_PIN["bytes"],
            "sha256": AMENDMENT_PIN["sha256"],
        },
        "inventory amendment chain",
    )
    _expect(
        amendment_003.get("pinnedPriorAmendment"),
        {
            "path": (
                "validation/"
                "omega-nnue-king-state-v3-amendment-002.json"
            ),
            "bytes": AMENDMENT_002_PIN["bytes"],
            "sha256": AMENDMENT_002_PIN["sha256"],
        },
        "inventory correction chain",
    )
    _expect(
        amendment_003.get("matchOperationalSafetyClarification"),
        {
            "changesSeedsRootsThresholdsOrDecisions": False,
            "adapterIsSoleAuthorizedOmegaMatchLauncher": True,
            "fixedInitialPairBudgets": {
                "development": 32,
                "equal-node": 64,
                "equal-time": 64,
            },
            "fixedResumePairBudget": 4,
            "pairBudgetMustBeMultipleOf": 4,
            "directOmegaMatchRunOrResumeForbidden": True,
            "appendOnlyLaunchIntentAndCompletionChainRequired": True,
            "assessmentRequiredBetweenLaunches": True,
            "assessmentEventIdentityMustEqualLatestLaunchCompletion": True,
            "assessmentProgressDeltaBoundedByLaunchPairBudget": True,
            "resumeRequiresLatestNonterminalExactAssessment": True,
            (
                "authorizationRepairIsIdempotentOnlyBefore"
                "SuccessorEvidence"
            ): True,
            "existingAuthorizationRepairPerformsVerificationOnly": True,
            (
                "equalTimeIdleAttestationMustNotPrecede"
                "EqualNodeDecision"
            ): True,
            (
                "terminalAssessmentDecisionPublicationRecovery"
                "CreatesNoNewAssessment"
            ): True,
            (
                "candidateRuntimeMustProveLoadedAndActiveNnue"
                "WithExactNetworkHash"
            ): True,
            (
                "controlRuntimeMustProveInactiveNnue"
                "WithNoExternalAssets"
            ): True,
            (
                "controlFinalStartupDiagnosticMustProve"
                "HandcraftedEvaluationActive"
            ): True,
            "managedLauncherUsesPinnedDotnetSelectionNamespaces": True,
            "managedLauncherSanitizesAmbientDotnetEnvironment": True,
            "managedLauncherRehashesAppBundleBeforeAndAfter": True,
            "purpose": (
                "Prevent a cumulative pair-budget mistake, skipped "
                "sequential checkpoint, direct or unbudgeted event growth, "
                "stale equal-time idle attestation, premature successor "
                "launch, or interruption between an immutable passing "
                "decision or idle attestation and its successor "
                "authorization from invalidating the one-shot confirmation "
                "sequence."
            ),
        },
        "match operational safety clarification",
    )
    expected_prebuilt_tools: dict[str, Any] = {
        "noPostFreezeDotnetBuild": True,
        "directExecutionFromFrozenArchive": True,
        "appLocalBundleIncludesEveryRegularFile": True,
        "symlinkAndReparseEntriesForbidden": True,
    }
    for tool_name, pins in (
        ("rootSampler", ROOT_SAMPLER_PINS),
        ("historySnapshot", HISTORY_SNAPSHOT_PINS),
        ("omegaMatch", OMEGA_MATCH_PINS),
    ):
        relative_pins: dict[str, dict[str, Any]] = {}
        for key, pin in pins.items():
            relative = dict(pin)
            relative["path"] = os.path.relpath(
                _resolve(Path(str(pin["path"]))), REPO
            ).replace("\\", "/")
            relative_pins[key] = relative
        expected_prebuilt_tools[tool_name] = relative_pins
    _expect(
        amendment_003.get("pinnedPrebuiltTooling"),
        expected_prebuilt_tools,
        "pinned prebuilt tooling declaration",
    )
    expected_dotnet_runtime: dict[str, Any] = {
        "postFreezeSdkOrMsbuildUseForbidden": True,
        "selectionNamespaceVersionDirectoriesPinned": True,
    }
    for key, pin in DOTNET_RUNTIME_PINS.items():
        relative = dict(pin)
        relative["path"] = os.path.relpath(
            _resolve(Path(str(pin["path"]))), REPO
        ).replace("\\", "/")
        expected_dotnet_runtime[key] = relative
    expected_dotnet_runtime["requiredAbsentRuntimePaths"] = {
        name: os.path.relpath(path, REPO).replace("\\", "/")
        for name, path in DOTNET_REQUIRED_ABSENT_PATHS.items()
    }
    managed_environment = copy.deepcopy(DOTNET_ENVIRONMENT_POLICY)
    set_variables = managed_environment.get("setVariables")
    if not isinstance(set_variables, dict):
        raise ValueError("managed subprocess environment is malformed")
    set_variables["DOTNET_ROOT"] = os.path.relpath(
        _resolve(DOTNET.parent), REPO
    ).replace("\\", "/")
    expected_dotnet_runtime["managedSubprocessEnvironment"] = (
        managed_environment
    )
    _expect(
        amendment_003.get("pinnedExecutionRuntime"),
        expected_dotnet_runtime,
        "pinned execution runtime declaration",
    )
    _verify_prebuilt_tooling()
    inventory_contract = amendment_002.get(
        "targetOpaqueInventoryFieldContract"
    )
    if not isinstance(inventory_contract, dict):
        raise ValueError("inventory amendment has no field contract")
    scalar_fields = inventory_contract.get(
        "caseInsensitiveScalarOfenFields"
    )
    containers = inventory_contract.get("selectedStringLeafContainers")
    opaque_metadata = inventory_contract.get(
        "explicitMetadataLookalikesKeptOpaque"
    )
    if (
        not isinstance(scalar_fields, list)
        or len(scalar_fields) != len(AMENDMENT_002_OFEN_FIELDS)
        or set(scalar_fields) != set(AMENDMENT_002_OFEN_FIELDS)
    ):
        raise ValueError("inventory amendment scalar fields changed")
    if (
        not isinstance(containers, list)
        or len(containers) != len(POSITION_CONTAINER_FIELDS)
        or set(containers) != set(POSITION_CONTAINER_FIELDS)
    ):
        raise ValueError("inventory amendment containers changed")
    if (
        not isinstance(opaque_metadata, list)
        or len(opaque_metadata)
        != len(AMENDMENT_002_OPAQUE_POSITION_METADATA_FIELDS)
        or set(opaque_metadata)
        != set(AMENDMENT_002_OPAQUE_POSITION_METADATA_FIELDS)
    ):
        raise ValueError(
            "inventory amendment opaque metadata allowlist changed"
        )
    declared_classes = (
        OFEN_FIELDS,
        POSITION_CONTAINER_FIELDS,
        OPAQUE_POSITION_METADATA_FIELDS,
    )
    for index, left in enumerate(declared_classes):
        for right in declared_classes[index + 1 :]:
            if left & right:
                raise ValueError(
                    "inventory amendment position-like field classes "
                    "overlap"
                )
    completeness = inventory_contract.get(
        "positionLikeKeyCompleteness"
    )
    if not isinstance(completeness, dict):
        raise ValueError(
            "inventory amendment has no position-like key completeness "
            "contract"
        )
    _expect(
        completeness.get("inventoryTriggerSubstrings"),
        list(POSITION_LIKE_KEY_FRAGMENTS),
        "position-like key trigger substrings",
    )
    _expect(
        completeness.get("unknownTriggeredKeyAbortsBeforeLabels"),
        True,
        "unknown position-like key abort",
    )
    raw_guard = inventory_contract.get("skippedOrdinaryStringGuard")
    if not isinstance(raw_guard, dict):
        raise ValueError(
            "inventory amendment has no skipped-string shape guard"
        )
    for key in (
        "inspectRawLexicalBytesOnly",
        "recognizesJsonEscapedSlashAndUnicodeEscapedSlashOrBrackets",
        "matchingOrdinaryStringAbortsAsUnclassifiedPotentialPosition",
    ):
        _expect(raw_guard.get(key), True, f"skipped-string guard {key}")
    _expect(
        raw_guard.get("jsonDecodeOrdinaryString"),
        False,
        "skipped-string target opacity",
    )
    corrections = amendment_003.get(
        "targetOpaqueInventoryCorrections"
    )
    if not isinstance(corrections, dict):
        raise ValueError("inventory correction has no correction contract")
    expected_roots = [
        {
            "rootId": root_id,
            "workspaceRelativePath": (
                _resolve(path).relative_to(WORKSPACE).as_posix()
            ),
        }
        for root_id, path in AMENDMENT_003_ADDITIONAL_ROOTS
    ]
    _expect(
        corrections.get("requiredAdditionalRoots"),
        expected_roots,
        "inventory correction required roots",
    )
    destination_policy = corrections.get(
        "freshGenerationDestinationPolicy"
    )
    if not isinstance(destination_policy, dict):
        raise ValueError(
            "inventory correction has no fresh-destination policy"
        )
    _expect(
        destination_policy.get("destinations"),
        _generation3_destination_contract(),
        "inventory correction fresh destinations",
    )
    for key in (
        "recursiveExclusionFromAuthoritativeUnion",
        "recursiveExclusionFromWorkspaceComplement",
        "suppliedRootAtOrInsideDestinationAborts",
        "mustBeAbsentOrEmptyAtFreeze",
        "neverHistoricalEvidenceForGenerationThree",
        "postPublicationVerificationUsesSameExclusions",
    ):
        _expect(
            destination_policy.get(key),
            True,
            f"fresh-destination policy {key}",
        )
    for (
        _destination_id,
        base,
        relative_path,
        expected_path,
    ) in GENERATION3_DESTINATION_DECLARATIONS:
        root = REPO if base == "repository" else WORKSPACE
        _expect(
            _resolve(root / relative_path),
            _resolve(expected_path),
            f"fresh-destination resolved path {relative_path}",
        )
    root_contract = corrections.get("rootContract")
    if not isinstance(root_contract, dict):
        raise ValueError("inventory correction has no root contract")
    for key in (
        "everyOldAndNewDeclaredRootMustExistAsDirectory",
        "recursiveJsonAndJsonlEnumeration",
        "overlappingDeclaredRootsDeduplicatedByResolvedPath",
        "resolvedInitialAndFinalPathSetsMustMatch",
        "missingRootAbortsBeforeLabels",
        "catalogRecordsEveryResolvedRoot",
        "catalogRecordsStableArtifactCount",
        "catalogRecordsUnionBytes",
    ):
        _expect(root_contract.get(key), True, f"root contract {key}")
    canonicalization = corrections.get("keyCanonicalization")
    if not isinstance(canonicalization, dict):
        raise ValueError(
            "inventory correction has no key canonicalization contract"
        )
    _expect(
        canonicalization.get("pythonOperation"),
        "str.casefold",
        "key canonicalization operation",
    )
    for key in (
        "appliesToLiteralAndEscapedJsonObjectKeys",
        "occursBeforeTriggerAndExactClassComparison",
        "duplicateCaseFoldedObjectKeyAborts",
        "lowercaseOnlyForbidden",
        "unknownTriggeredKeyStillAbortsBeforeLabels",
    ):
        _expect(
            canonicalization.get(key),
            True,
            f"key canonicalization {key}",
        )
    correction_raw_guard = corrections.get(
        "skippedOrdinaryStringGuard"
    )
    if not isinstance(correction_raw_guard, dict):
        raise ValueError(
            "inventory correction has no skipped-string shape guard"
        )
    for key in (
        "inspectRawLexicalBytesOnly",
        "otherEscapesRemainOpaqueSentinels",
        "leadingLiteralOrEscapedUnicodeWhitespaceAccepted",
        "matchingOrdinaryStringAbortsAsUnclassifiedPotentialPosition",
        "ordinaryTargetAndPvStringsRemainOpaque",
    ):
        _expect(
            correction_raw_guard.get(key),
            True,
            f"corrected skipped-string guard {key}",
        )
    _expect(
        correction_raw_guard.get("jsonDecodeOrdinaryString"),
        False,
        "corrected skipped-string target opacity",
    )
    additional_opaque = corrections.get(
        "additionalOpaqueMetadataFields"
    )
    if (
        not isinstance(additional_opaque, list)
        or len(additional_opaque)
        != len(AMENDMENT_003_OPAQUE_POSITION_METADATA_FIELDS)
        or set(additional_opaque)
        != set(AMENDMENT_003_OPAQUE_POSITION_METADATA_FIELDS)
    ):
        raise ValueError(
            "inventory correction opaque metadata allowlist changed"
        )
    additional_scalar_ofen = corrections.get(
        "additionalScalarOfenFields"
    )
    if (
        not isinstance(additional_scalar_ofen, list)
        or len(additional_scalar_ofen)
        != len(AMENDMENT_003_ADDITIONAL_OFEN_FIELDS)
        or set(additional_scalar_ofen)
        != set(AMENDMENT_003_ADDITIONAL_OFEN_FIELDS)
    ):
        raise ValueError(
            "inventory correction scalar OFEN field declaration changed"
        )
    _expect(
        corrections.get("legacyFourFieldBoardKeyPolicy"),
        {
            "exactCaseFoldedFields": sorted(PARTIAL_OFEN_FIELDS),
            "acceptedWhitespaceDelimitedFieldCounts": [4, 6],
            "fourFieldCanonicalSuffix": list(
                PARTIAL_OFEN_CANONICAL_SUFFIX
            ),
            "canonicalizeBeforeValidationAndLeakageKeyDerivation": True,
            "strictProductionParserBeforeLeakageKeyDerivation": True,
            "preserveBoardSideCastlingAndEnPassantFields": True,
            "sixFieldValuesKeepTheirOriginalClockFields": True,
            "allOtherBoardStateFieldsRequireExactlySixFields": True,
            "allOtherFieldCountsAbortBeforeLabels": True,
            "exactCanonicalizationCountsRecordedByCaseFoldedField": True,
            "inputArityCountsFrozenBeforeLabels": True,
            "perSourceIdentityAndCountsFrozenBeforeLabels": True,
            "orderedExpansionEventProjectionFrozenBeforeLabels": True,
            "triggerReconciliationRequired": True,
            "conservativeLeakageKeysIgnoreClockFields": True,
        },
        "legacy four-field board-key canonicalization",
    )
    null_scalar_policy = corrections.get("nullScalarValuePolicy")
    if not isinstance(null_scalar_policy, dict):
        raise ValueError(
            "inventory correction has no scalar-null policy"
        )
    for key in (
        "jsonNullMeansAbsent",
        "nullValuesAreNotDecodedAsBoardStates",
        "exactNullCountsRecordedByCaseFoldedField",
        "allOtherNonStringScalarValuesAbort",
        "invalidStringScalarValuesAbort",
    ):
        _expect(
            null_scalar_policy.get(key),
            True,
            f"scalar-null policy {key}",
        )
    structural_policy = corrections.get(
        "structuralScalarAliasPolicy"
    )
    if not isinstance(structural_policy, dict):
        raise ValueError(
            "inventory correction has no structural-scalar alias policy"
        )
    expected_structural_sources: list[dict[str, Any]] = []
    for source in STRUCTURAL_SCALAR_ALIAS_SOURCES:
        _verify_identity(source, "structural scalar alias source")
        expected_source = {
            "path": os.path.relpath(
                _resolve(Path(str(source["path"]))), REPO
            ).replace("\\", "/"),
            "bytes": source["bytes"],
            "sha256": source["sha256"],
            "aliases": [dict(alias) for alias in source["aliases"]],
        }
        expected_structural_sources.append(expected_source)
    _expect(
        structural_policy,
        {
            "exactPinnedSourceIdentityRequired": True,
            "exactJsonPointerRequired": True,
            "exactCanonicalValueShapeRequired": True,
            "nestedKeysStillScannedRecursively": True,
            "triggerKeysStillCounted": True,
            "onlyPinnedMetadataObjectsDecoded": True,
            "wrongSourcePointerOrShapeAborts": True,
            "sources": expected_structural_sources,
            "expectedRoleCounts": [
                {
                    "role": role,
                    "occurrences": (
                        EXPECTED_STRUCTURAL_SCALAR_ALIAS_COUNTS[role]
                    ),
                }
                for role in sorted(
                    EXPECTED_STRUCTURAL_SCALAR_ALIAS_COUNTS
                )
            ],
        },
        "structural scalar alias policy",
    )
    replay = amendment_003.get("selectiveOpeningAndTranscriptReplay")
    if not isinstance(replay, dict):
        raise ValueError("inventory correction has no replay contract")
    _expect(replay.get("variant"), "Omega", "replay variant")
    _expect(
        replay.get("officialInitialOfen"),
        OMEGA_INITIAL_OFEN,
        "replay official initial OFEN",
    )
    for key in (
        "preserveEveryRawSourceOccurrence",
        "unrelatedMovePvAndSearchArraysRemainOpaque",
        "structuredObjectMoveLogsRemainOpaqueAfterNestedBoardScan",
        "unknownAmbiguousOrDuplicateRecognizedFieldsAbort",
        "nonStringBlankOrUntrimmedMovesAbort",
    ):
        _expect(replay.get(key), True, f"replay contract {key}")
    _expect(
        replay.get("deduplicateProjectionRequests"),
        False,
        "replay raw-occurrence preservation",
    )
    _expect(
        replay.get("maximumMovesPerSequence"),
        512,
        "replay move cap",
    )
    _expect(
        replay.get("helperTimeoutSeconds"),
        OPENING_REPLAY_TIMEOUT_SECONDS,
        "replay helper timeout",
    )
    source_schemas = replay.get("sourceSchemas")
    if (
        not isinstance(source_schemas, list)
        or [
            value.get("schema")
            for value in source_schemas
            if isinstance(value, dict)
        ]
        != [
            "schedule-moves",
            "event-opening-moves",
            "transcript",
        ]
    ):
        raise ValueError("replay source schema inventory changed")
    projection = replay.get("projection")
    if not isinstance(projection, dict):
        raise ValueError("replay projection contract is absent")
    _expect(
        projection.get("kind"),
        OPENING_REPLAY_REQUEST_KIND,
        "replay request kind",
    )
    _expect(
        projection.get("exactFields"),
        [
            "schemaVersion",
            "kind",
            "requestId",
            "sourcePath",
            "sourceBytes",
            "sourceSha256",
            "sourceRecord",
            "sourceObjectOrdinal",
            "sourceObjectIdentity",
            "schema",
            "initialSource",
            "containerProof",
            "initialOfen",
            "moves",
            "expectedPositions",
        ],
        "replay projection fields",
    )
    replay_output = replay.get("output")
    replay_legality = replay.get("legality")
    replay_publication = replay.get("publication")
    if not all(
        isinstance(value, dict)
        for value in (
            replay_output,
            replay_legality,
            replay_publication,
        )
    ):
        raise ValueError("replay output/legality/publication contract absent")
    _expect(
        replay_output.get("kind"),
        OPENING_REPLAY_PREFIX_KIND,
        "replay prefix kind",
    )
    for key in (
        "includesPlyZero",
        "includesEveryPostMovePrefix",
        "coordinateMustMatchAppliedMove",
        "fullSixFieldTranscriptParityRequiredAtEveryPly",
    ):
        _expect(replay_output.get(key), True, f"replay output {key}")
    for key in (
        "pinnedOmegaRulesOnly",
        "malformedOrIllegalMoveAborts",
        "terminalInitialStateCannotBeContinued",
        "continuationAfterCheckmateStalemateOrDrawAborts",
        "finalTerminalStateAllowed",
    ):
        _expect(
            replay_legality.get(key), True, f"replay legality {key}"
        )
    _expect(
        replay_publication.get("manifestKind"),
        OPENING_REPLAY_MANIFEST_KIND,
        "replay manifest kind",
    )
    for key in (
        "inputOutputAndManifestPathsMustDiffer",
        "outputAndManifestUseCreateNewNoClobber",
        "secondPublicationFailureDeletesNewlyPublishedOutput",
        "ordinaryFailureRollsBackArtifactsCreatedByThisInvocation",
        "sourceAndProjectionIdentitiesVerifiedBeforeAndAfterReplay",
        "outputManifestAndRuntimeIdentitiesLinkedAndReverified",
        "fullPinnedRuntimeBundleReverifiedAfterReplay",
        "emptyKnownEvidenceProjectionAborts",
    ):
        _expect(
            replay_publication.get(key),
            True,
            f"replay publication {key}",
        )
    _expect(
        replay_publication.get("crossProcessAtomicPairClaimed"),
        False,
        "replay publication atomicity claim",
    )
    replay_implementation = amendment_003.get(
        "pinnedOpeningReplayImplementation"
    )
    if not isinstance(replay_implementation, dict):
        raise ValueError("replay implementation pins are absent")
    _expect(
        replay_implementation.get("coreChessHeadCommitAtBuild"),
        OPENING_REPLAY_CORECHESS_HEAD_COMMIT,
        "replay CoreChess HEAD anchor",
    )
    _expect(
        replay_implementation.get("coreChessWorkingTreeAtBuild"),
        "dirty",
        "replay CoreChess working-tree state",
    )
    _expect(
        replay_implementation.get("authoritativeRulesIdentityBasis"),
        (
            "exact Game.cs, ChessLib.csproj, and ChessLib.dll identities; "
            "HEAD commit is a provenance anchor only"
        ),
        "replay authoritative rules identity basis",
    )
    declared_replay_artifacts = replay_implementation.get("artifacts")
    if not isinstance(declared_replay_artifacts, dict):
        raise ValueError("replay artifact pins are absent")
    expected_replay_artifacts: dict[str, dict[str, Any]] = {}
    for key, pin in OPENING_REPLAY_PINS.items():
        relative = dict(pin)
        relative["path"] = os.path.relpath(
            _resolve(Path(str(pin["path"]))), REPO
        ).replace("\\", "/")
        expected_replay_artifacts[key] = relative
        _verify_identity(pin, f"declared opening replay {key}")
    _expect(
        declared_replay_artifacts,
        expected_replay_artifacts,
        "replay implementation artifact pins",
    )
    expected_runtime_bundle = dict(
        OPENING_REPLAY_RUNTIME_BUNDLE_PIN
    )
    expected_runtime_bundle["path"] = os.path.relpath(
        _resolve(
            Path(str(OPENING_REPLAY_RUNTIME_BUNDLE_PIN["path"]))
        ),
        REPO,
    ).replace("\\", "/")
    _expect(
        replay_implementation.get("appLocalRuntimeBundle"),
        expected_runtime_bundle,
        "replay app-local runtime bundle declaration",
    )
    _expect(
        _opening_replay_runtime_bundle_identity(),
        OPENING_REPLAY_RUNTIME_BUNDLE_PIN,
        "replay app-local runtime bundle",
    )
    _expect(
        replay_implementation.get("selfTestPassed"),
        True,
        "replay helper self-test",
    )
    complement_contract = amendment_003.get(
        "workspaceComplementSentinel"
    )
    if not isinstance(complement_contract, dict):
        raise ValueError("workspace complement sentinel is absent")
    _expect(
        complement_contract.get("triggerKeys"),
        sorted(COMPLEMENT_SENTINEL_KEYS),
        "workspace complement trigger keys",
    )
    for key in (
        "rawLexicalStringTokenScan",
        "freshGenerationDestinationSubtreesExcludedRecursively",
        "unclassifiedRawBoardStringShapeMatchAborts",
        "unclassifiedTriggerKeyMatchAborts",
        "exactClassifiedAliasesAllowed",
        "initialAndFinalComplementPathSetsMustMatch",
        "catalogRecordsComplementCountBytesAndOrderedIdentitySetHash",
        "catalogRecordsClassCountsKeyTotalsRawTotalsAndZeroUnclassified",
        "authoritativeSourceCoverageReverified",
    ):
        _expect(
            complement_contract.get(key),
            True,
            f"workspace complement {key}",
        )
    safety_proofs = {
        "generated-canonical-public-opening-copy": (
            "byte-identical to the pinned current authoritative canonical "
            "opening artifact"
        ),
        "generated-style-public-opening-copy": (
            "strict-JSON canonical bytes equal the pinned current "
            "authoritative canonical opening artifact"
        ),
        "generated-omega-rules-fixture-copy": (
            "byte-identical to the pinned current authoritative Omega "
            "rules fixture"
        ),
        "translation-en-core": (
            "exact identity with one scalar translation string and no raw "
            "board-string shape"
        ),
        "translation-it-core": (
            "exact identity with one scalar translation string and no raw "
            "board-string shape"
        ),
        "translation-en-omega": (
            "exact identity with one scalar translation string and no raw "
            "board-string shape"
        ),
        "translation-it-omega": (
            "exact identity with one scalar translation string and no raw "
            "board-string shape"
        ),
    }
    expected_alias_requirements: list[dict[str, Any]] = []
    for sha256, allowed in COMPLEMENT_ALLOWED_HIT_CLASSES.items():
        class_name = str(allowed["class"])
        expected_alias_requirements.append(
            {
                "class": class_name,
                "occurrences": EXPECTED_COMPLEMENT_CLASS_COUNTS[
                    class_name
                ],
                "bytes": allowed["bytes"],
                "sha256": sha256,
                "expectedKeyHits": allowed["keys"],
                "expectedValueTypes": [
                    {
                        "field": field_type.split(":", 1)[0],
                        "type": field_type.split(":", 1)[1],
                        "occurrences": occurrences,
                    }
                    for field_type, occurrences in dict(
                        allowed["valueTypes"]
                    ).items()
                ],
                "rawBoardStringShapeHits": allowed[
                    "rawOfenShapeHits"
                ],
                "safetyProof": safety_proofs[class_name],
            }
        )
    _expect(
        complement_contract.get("classifiedAliasRequirements"),
        expected_alias_requirements,
        "workspace complement classified aliases",
    )
    evidence = amendment_003.get("classifiedExistingEvidence")
    if not isinstance(evidence, dict):
        raise ValueError("inventory correction evidence is absent")
    _expect(
        evidence.get(
            "freshGenerationDestinationsAbsentOrEmptyAtFreeze"
        ),
        True,
        "fresh generation destinations absent at freeze",
    )
    if mode == "frozen":
        _expect(
            evidence.get("authoritativeUnion"),
            {
                "artifacts": EXPECTED_PRIOR_ARTIFACT_COUNT,
                "bytes": EXPECTED_PRIOR_ARTIFACT_BYTES,
                "stablePathSetRequired": True,
            },
            "authoritative union evidence",
        )
    else:
        _expect(
            evidence.get("authoritativeUnion"),
            {
                "artifacts": EXPECTED_PRIOR_ARTIFACT_COUNT,
                "bytes": None,
                "stablePathSetRequired": True,
            },
            "draft authoritative union evidence",
        )
    replay_evidence = evidence.get("openingAndTranscriptReplay")
    if not isinstance(replay_evidence, dict):
        raise ValueError("replay evidence is absent")
    for key, expected in EXPECTED_REPLAY_AUDIT.items():
        evidence_key = {
            "nullScalarValueCounts": "nullScalarValueCountEntries",
            "partialOfenCanonicalizationCount": (
                "legacyFourFieldBoardKeyCount"
            ),
            "partialOfenCanonicalizationCounts": (
                "legacyFourFieldBoardKeyCountEntries"
            ),
        }.get(key, key)
        expected_value = (
            _count_evidence(expected)
            if key
            in {
                "nullScalarValueCounts",
                "partialOfenCanonicalizationCounts",
            }
            else expected
        )
        _expect(
            replay_evidence.get(evidence_key),
            expected_value,
            f"declared replay evidence {key}",
        )
    for forbidden_map in (
        "nullScalarValueCounts",
        "partialOfenCanonicalizationCounts",
    ):
        if forbidden_map in replay_evidence:
            raise ValueError(
                "replay field-count telemetry must use target-neutral "
                "entries"
            )
    _expect(
        replay_evidence.get("fullSixFieldTranscriptParityMismatches"),
        0,
        "declared transcript parity",
    )
    expected_complement_evidence = {
        "artifacts": EXPECTED_COMPLEMENT_ARTIFACT_COUNT,
        "bytes": EXPECTED_COMPLEMENT_ARTIFACT_BYTES,
        "orderedIdentitySet": EXPECTED_COMPLEMENT_IDENTITY_SET,
        "classifiedHitArtifacts": sum(
            EXPECTED_COMPLEMENT_CLASS_COUNTS.values()
        ),
        "classifiedHitOrderedIdentitySet": (
            EXPECTED_COMPLEMENT_CLASSIFIED_IDENTITY_SET
        ),
        "classCounts": EXPECTED_COMPLEMENT_CLASS_COUNTS,
        "keyHits": EXPECTED_COMPLEMENT_KEY_HITS,
        "rawBoardStringShapeHits": (
            EXPECTED_COMPLEMENT_RAW_BOARD_SHAPE_HITS
        ),
        "unclassifiedHitArtifacts": 0,
        "authoritativeCoverage": {
            "canonicalPublicOpenings": {
                "path": (
                    "corechess-arena/Tools/OmegaMatch/Openings/"
                    "omega-public-24.json"
                ),
                "bytes": 2996,
                "sha256": (
                    "840faa76475e8297eb73eb33805fabf98202a5002143bb"
                    "19570f9ec5aec95955"
                ),
            },
            "omegaRulesFixture": {
                "path": "fixtures/omega-rules.json",
                "bytes": 647,
                "sha256": (
                    "51dfc67c6abd917342bd07cc1e7ce2928a4660315b62c"
                    "f3b8194e12854aba939"
                ),
            },
        },
    }
    _expect(
        evidence.get("workspaceComplement"),
        expected_complement_evidence,
        "workspace complement frozen evidence",
    )
    if mode == "frozen":
        if EXPECTED_FINAL_TARGET_FREE_INVENTORY is None:
            raise ValueError(
                "final target-free inventory expectation is not frozen"
            )
        _expect(
            evidence.get("finalTargetFreeInventory"),
            EXPECTED_FINAL_TARGET_FREE_INVENTORY,
            "final target-free inventory frozen evidence",
        )
    freeze_procedure = amendment_003.get("freezeProcedure")
    if not isinstance(freeze_procedure, dict):
        raise ValueError("inventory correction freeze procedure is absent")
    for key in (
        "proposalIsReadOnlyByDefault",
        "optionalProposalOutputMustBeExternalCreateNew",
        "singleSourceStableTargetFreeScanCachedInMemory",
        "freshDestinationsAbsentOrEmptyAtProposalStartAndEnd",
        "candidateDocumentsRecomputedWithoutRescanningHistoricalPayloads",
        "amendmentByteCountMustRemainExactlyFiveDigits",
        "verificationBeforeFirstGeneration3Publication",
    ):
        _expect(
            freeze_procedure.get(key),
            True,
            f"inventory correction freeze procedure {key}",
        )
    _expect(
        freeze_procedure.get("completeFixedPointStatesRequired"),
        2,
        "inventory correction fixed-point repeats",
    )
    _expect(
        freeze_procedure.get("maximumFixedPointPasses"),
        8,
        "inventory correction fixed-point pass limit",
    )
    _expect(
        freeze_procedure.get("freshEndToEndVerificationProcesses"),
        2,
        "inventory correction fresh verification process count",
    )
    _expect(
        freeze_procedure.get("proposalCommand"),
        (
            "python tools/omega_nnue/king_state_v3.py "
            "propose-amendment-003-freeze --created-utc <strict-utc-z>"
        ),
        "inventory correction proposal command",
    )
    _expect(
        freeze_procedure.get("verificationCommand"),
        (
            "python tools/omega_nnue/king_state_v3.py "
            "verify-amendment-003"
        ),
        "inventory correction verification command",
    )
    _expect(
        freeze_procedure.get("trackedTransactionFiles"),
        [
            "validation/omega-nnue-king-state-v3-amendment-003.json",
            "validation/omega-nnue-king-state-v3-match-protocol.json",
            "validation/omega-nnue-king-state-v3-match-adapter.json",
            "tools/omega_nnue/king_state_v3.py",
            "tools/omega_nnue/king_state_train_generation3.py",
            (
                "tools/omega_nnue/"
                "king_state_match_readiness_generation3.py"
            ),
        ],
        "inventory correction tracked transaction files",
    )
    _expect(
        {
            "targetFieldsDecoded": freeze_procedure.get(
                "targetFieldsDecoded"
            ),
            "targetFieldsEmitted": freeze_procedure.get(
                "targetFieldsEmitted"
            ),
        },
        {"targetFieldsDecoded": 0, "targetFieldsEmitted": 0},
        "inventory correction freeze information boundary",
    )
    pins = amendment.get("pinnedDeclarations")
    if not isinstance(pins, dict):
        raise ValueError("amendment has no declaration pins")
    for key, expected in (
        ("preregistration", PREREG_PIN),
        ("generation2OfflineIncident", INCIDENT_PIN),
        ("phaseIncidenceImplementationAtAmendment", PHASE_IMPLEMENTATION_PIN),
    ):
        actual = pins.get(key)
        if not isinstance(actual, dict):
            raise ValueError(f"amendment is missing {key}")
        relative_expected = dict(expected)
        relative_expected["path"] = _resolve(
            Path(str(expected["path"]))
        ).relative_to(REPO).as_posix()
        _expect(actual, relative_expected, f"amendment {key}")
    fresh = profile.get("freshTeacherGeneration")
    if not isinstance(fresh, dict):
        raise ValueError("profile has no fresh-teacher contract")
    for key, expected in {
        "dataProfile": "deep-hce-v4",
        "directoryName": "deep-hce-v4",
        "samplerAndSelectorSeed": SEED,
        "targetPairs": TARGET_PAIRS,
        "targetPairsPerPhase": TARGET_PAIRS_PER_PHASE,
        "reservePairsPerPhase": RESERVE_PAIRS_PER_PHASE,
    }.items():
        _expect(fresh.get(key), expected, f"profile fresh teacher {key}")
    _expect(
        fresh.get("teacher"),
        {
            "sameFrozenSenpaiHceExecutableForEveryRoot": True,
            "UseOmegaNNUE": False,
            "OmegaNNUEFile": "<empty>",
            "Threads": 1,
            "HashMiB": 128,
            "OwnBook": False,
            "Ponder": False,
            "UciVariant": "omega",
            "fixedNodesPerRoot": TEACHER_NODES,
            "acceptExactCpOnly": True,
            "rejectMateScores": True,
            "rejectBoundScores": True,
            "rejectShortNodeSearches": True,
            "wholePairExclusionOnInvalidMember": True,
        },
        "profile fresh teacher engine contract",
    )
    _expect(
        deep.HCE_OPTIONS,
        {
            "Threads": "1",
            "Hash": "128",
            "Ponder": "false",
            "OwnBook": "false",
            "UCI_Chess960": "false",
            "UCI_Variant": "omega",
            "OmegaNNUEFile": "<empty>",
            "UseOmegaNNUE": "false",
        },
        "installed fresh teacher HCE options",
    )
    fixed = (
        amendment.get("freshTeacherClarifications", {})
        .get("fixedParameters", {})
    )
    for key, expected in {
        "samplerAndSelectorSeed": SEED,
        "targetPairs": TARGET_PAIRS,
        "targetPairsPerPhase": TARGET_PAIRS_PER_PHASE,
        "reservePairsPerPhase": RESERVE_PAIRS_PER_PHASE,
        "teacherNodesPerRoot": TEACHER_NODES,
        "preflightExtraPairsPerPhase": PREFLIGHT_EXTRA_PAIRS_PER_PHASE,
        "preflightRejectedPairSanityCap": MAX_PREFLIGHT_REJECTED_PAIRS,
        "maximumSelectedPairsPerTrajectory": MAX_PAIRS_PER_TRAJECTORY,
        "maximumSelectedPairsPerSplitGroup": MAX_PAIRS_PER_SPLIT_GROUP,
        "candidatePairsPerTrajectoryPerPhase": (
            CANDIDATE_PAIRS_PER_TRAJECTORY_PHASE
        ),
        "acceptanceTimeoutSeconds": int(ACCEPTANCE_TIMEOUT_SECONDS),
    }.items():
        _expect(fixed.get(key), expected, f"amendment fixed {key}")
    source_policy = (
        amendment.get("freshTeacherClarifications", {})
        .get("sourcePolicy", {})
    )
    _expect(
        source_policy.get("historicalEventsAllowed"),
        False,
        "historical source policy",
    )
    _expect(
        amendment.get("phaseIncidenceClarification", {}).get(
            "minimumGroupsPerObservedStratumPerSplit"
        ),
        2,
        "per-split incidence minimum",
    )
    _validate_match_declaration_transaction(AMENDMENT_003_PIN)
    return profile, amendment


def _decode_json_string(
    text: str, start: int, end: int, *, location: str
) -> str:
    return incidence._decode_json_string(  # type: ignore[attr-defined]
        text, start, end, location=location
    )


def _walk_lexical_value(
    text: str,
    start: int,
    *,
    location: str,
    key: str | None = None,
) -> tuple[int, list[str]]:
    """Walk JSON and decode only keys plus OFEN-family string values."""

    index = incidence._skip_space(text, start)  # type: ignore[attr-defined]
    if index >= len(text):
        raise ValueError(f"{location}: missing JSON value")
    character = text[index]
    if character == '"':
        end = incidence._skip_json_string(  # type: ignore[attr-defined]
            text, index, location=location
        )
        if key is not None and key.casefold() in OFEN_FIELDS:
            return end, [
                _decode_json_string(
                    text, index, end, location=location
                )
            ]
        return end, []
    if character == "{":
        found: list[str] = []
        index = incidence._skip_space(  # type: ignore[attr-defined]
            text, index + 1
        )
        if index < len(text) and text[index] == "}":
            return index + 1, found
        while True:
            key_start = index
            key_end = incidence._skip_json_string(  # type: ignore[attr-defined]
                text, key_start, location=location
            )
            child_key = _decode_json_string(
                text, key_start, key_end, location=location
            )
            index = incidence._skip_space(  # type: ignore[attr-defined]
                text, key_end
            )
            if index >= len(text) or text[index] != ":":
                raise ValueError(f"{location}: missing object colon")
            index, values = _walk_lexical_value(
                text, index + 1, location=location, key=child_key
            )
            found.extend(values)
            index = incidence._skip_space(  # type: ignore[attr-defined]
                text, index
            )
            if index < len(text) and text[index] == ",":
                index = incidence._skip_space(  # type: ignore[attr-defined]
                    text, index + 1
                )
                continue
            if index < len(text) and text[index] == "}":
                return index + 1, found
            raise ValueError(f"{location}: malformed JSON object")
    if character == "[":
        found = []
        index = incidence._skip_space(  # type: ignore[attr-defined]
            text, index + 1
        )
        if index < len(text) and text[index] == "]":
            return index + 1, found
        while True:
            index, values = _walk_lexical_value(
                text, index, location=location
            )
            found.extend(values)
            index = incidence._skip_space(  # type: ignore[attr-defined]
                text, index
            )
            if index < len(text) and text[index] == ",":
                index = incidence._skip_space(  # type: ignore[attr-defined]
                    text, index + 1
                )
                continue
            if index < len(text) and text[index] == "]":
                return index + 1, found
            raise ValueError(f"{location}: malformed JSON array")
    end = incidence._skip_json_value(  # type: ignore[attr-defined]
        text, index, location=location
    )
    return end, []


def _lexical_ofens(text: str, *, location: str) -> list[str]:
    end, values = _walk_lexical_value(
        text.lstrip("\ufeff"), 0, location=location
    )
    normalized = text.lstrip("\ufeff")
    end = incidence._skip_space(  # type: ignore[attr-defined]
        normalized, end
    )
    if end != len(normalized):
        raise ValueError(f"{location}: trailing content after JSON value")
    return values


class _LexicalJsonTokens:
    """Complete JSON tokenizer that never materializes scalar values."""

    def __init__(self, text: str, *, location: str) -> None:
        self.text = text.lstrip("\ufeff")
        self.location = location
        self.index = 0
        self.buffer: tuple[str, int, int, str] | None = None

    def _read(self) -> tuple[str, int, int, str] | None:
        if self.index == len(self.text):
            return None
        match = JSON_TOKEN.match(self.text, self.index)
        if match is None:
            whitespace = JSON_WHITESPACE_TO_END.match(
                self.text, self.index
            )
            if whitespace is not None:
                self.index = len(self.text)
                return None
            excerpt = self.text[self.index : self.index + 24]
            raise ValueError(
                f"{self.location}: invalid JSON token at character "
                f"{self.index}: {excerpt!r}"
            )
        self.index = match.end()
        kind = str(match.lastgroup)
        start, end = match.span(kind)
        return kind, start, end, match.group(kind)

    def peek(self) -> tuple[str, int, int, str] | None:
        if self.buffer is None:
            self.buffer = self._read()
        return self.buffer

    def pop(self) -> tuple[str, int, int, str]:
        token = self.peek()
        if token is None:
            raise ValueError(f"{self.location}: unexpected end of JSON")
        self.buffer = None
        return token


def _raw_ofen_shape_characters(
    text: str, start: int, end: int
) -> Iterator[str]:
    """Yield only shape-relevant characters from a raw JSON string token."""

    index = start + 1
    limit = end - 1
    while index < limit:
        character = text[index]
        if character != "\\":
            if character.isspace():
                yield " "
            elif character.isascii() and (
                character.isalnum() or character in "-/[]"
            ):
                yield character
            else:
                yield "\x00"
            index += 1
            continue
        escape = text[index + 1]
        if escape == "/":
            yield "/"
            index += 2
            continue
        if escape in "tfnr":
            yield " "
            index += 2
            continue
        if escape == "u":
            candidate = chr(int(text[index + 2 : index + 6], 16))
            if candidate.isspace():
                yield " "
            elif candidate.isascii() and (
                candidate.isalnum() or candidate in "-/[]"
            ):
                yield candidate
            else:
                yield "\x00"
            index += 6
            continue
        yield "\x00"
        index += 2


def _raw_string_has_omega_ofen_shape(
    text: str, start: int, end: int
) -> bool:
    """Recognize an OFEN prefix without decoding an ordinary string value."""

    characters = iter(_raw_ofen_shape_characters(text, start, end))
    current = next(characters, None)

    def advance() -> None:
        nonlocal current
        current = next(characters, None)

    while current == " ":
        advance()
    for rank in range(10):
        if not (
            current is not None
            and current.isascii()
            and current.isalnum()
        ):
            return False
        while (
            current is not None
            and current.isascii()
            and current.isalnum()
        ):
            advance()
        if rank < 9:
            if current != "/":
                return False
            advance()
    if current != "[":
        return False
    advance()
    while current is not None and (
        (current.isascii() and current.isalnum())
        or current in "-/"
    ):
        advance()
    if current != "]":
        return False
    advance()
    if current != " ":
        return False
    while current == " ":
        advance()
    if current not in ("w", "b"):
        return False
    advance()
    return current is None or current == " "


def _canonicalize_scalar_ofen(
    key: str,
    value: str,
    *,
    location: str,
) -> tuple[str, bool]:
    """Return a strict six-field OFEN and whether a legacy key was expanded."""

    fields = value.split()
    canonicalized = key in PARTIAL_OFEN_FIELDS and len(fields) == 4
    if canonicalized:
        candidate = " ".join((*fields, *PARTIAL_OFEN_CANONICAL_SUFFIX))
    else:
        if len(fields) != 6:
            raise ValueError(
                f"{location}: OFEN-family field {key!r} must have exactly "
                "six fields unless it is a declared four-field legacy "
                "position identity"
            )
        candidate = value
    try:
        parse_ofen(candidate)
        deep._leakage_keys(candidate)
    except (TypeError, ValueError) as error:
        raise ValueError(
            f"{location}: OFEN-family string is not a valid Omega OFEN"
        ) from error
    return candidate, canonicalized


def _lexical_scan_validated(
    text: str,
    *,
    location: str,
    replay_requests: list[dict[str, Any]] | None = None,
    source_record: int = 1,
    null_scalar_value_counts: Counter[str] | None = None,
    partial_ofen_canonicalization_counts: Counter[str] | None = None,
    scalar_ofen_arity_counts: Counter[tuple[str, int]] | None = None,
    partial_ofen_canonicalization_events: (
        list[dict[str, Any]] | None
    ) = None,
    source_identity: Mapping[str, Any] | None = None,
    structural_alias_counts: Counter[str] | None = None,
    structural_alias_field_counts: Counter[str] | None = None,
    structural_alias_lookup: (
        Mapping[tuple[str, str], Mapping[str, Any]] | None
    ) = None,
) -> tuple[list[str], Counter[str]]:
    """Validate a complete JSON value and decode keys plus OFEN values only."""

    tokens = _LexicalJsonTokens(text, location=location)
    found: list[str] = []
    position_like_key_counts: Counter[str] = Counter()
    object_ordinal = 0
    alias_lookup = (
        structural_alias_lookup
        if structural_alias_lookup is not None
        else _structural_scalar_alias_lookup(source_identity)
    )

    def key_name_and_role(
        start: int, end: int
    ) -> tuple[str, str, str]:
        raw = tokens.text[start:end]
        if "\\" not in raw:
            original_key = raw[1:-1]
        else:
            original_key = _decode_json_string(
                tokens.text, start, end, location=location
            )
        key = original_key.casefold()
        if any(fragment in key for fragment in POSITION_LIKE_KEY_FRAGMENTS):
            position_like_key_counts[key] += 1
        if key in OFEN_FIELDS:
            return key, original_key, "ofen"
        if key in POSITION_CONTAINER_FIELDS:
            return key, original_key, "position-container"
        return key, original_key, "ordinary"

    def punctuation(expected: str) -> None:
        kind, _start, _end, value = tokens.pop()
        if kind != "punctuation" or value != expected:
            raise ValueError(
                f"{location}: expected {expected!r}, got {value!r}"
            )

    def decoded_direct_string(
        span: tuple[int, int, str], label: str
    ) -> str:
        start, end, kind = span
        if kind != "string":
            raise ValueError(
                f"{location}: recognized replay {label} is not a string"
            )
        return _decode_json_string(
            tokens.text, start, end, location=location
        )

    def decoded_move_array(
        span: tuple[int, int, str], label: str
    ) -> list[str]:
        start, end, kind = span
        if kind != "array":
            raise ValueError(
                f"{location}: recognized replay {label} is not an array"
            )
        selected = _LexicalJsonTokens(
            tokens.text[start:end],
            location=f"{location}:{label}",
        )
        array_open = selected.pop()
        if (
            array_open[0] != "punctuation"
            or array_open[3] != "["
        ):
            raise ValueError(
                f"{location}: recognized replay {label} is malformed"
            )
        moves: list[str] = []
        next_token = selected.peek()
        if (
            next_token is not None
            and next_token[0] == "punctuation"
            and next_token[3] == "]"
        ):
            selected.pop()
        else:
            while True:
                move_kind, move_start, move_end, _move_token = (
                    selected.pop()
                )
                if move_kind != "string":
                    raise ValueError(
                        f"{location}: recognized replay {label} "
                        "contains a non-string move"
                    )
                move = _decode_json_string(
                    selected.text,
                    move_start,
                    move_end,
                    location=f"{location}:{label}",
                )
                if not move or move != move.strip():
                    raise ValueError(
                        f"{location}: recognized replay {label} "
                        "contains a blank or untrimmed move"
                    )
                moves.append(move)
                separator = selected.pop()
                if (
                    separator[0] == "punctuation"
                    and separator[3] == "]"
                ):
                    break
                if (
                    separator[0] != "punctuation"
                    or separator[3] != ","
                ):
                    raise ValueError(
                        f"{location}: recognized replay {label} "
                        "has a malformed separator"
                    )
        if selected.peek() is not None:
            raise ValueError(
                f"{location}: recognized replay {label} has trailing data"
            )
        if len(moves) > 512:
            raise ValueError(
                f"{location}: recognized replay {label} is too long"
            )
        return moves

    def decoded_position_array(
        span: tuple[int, int, str], label: str
    ) -> list[str]:
        start, end, kind = span
        if kind != "array":
            raise ValueError(
                f"{location}: recognized replay {label} is not an array"
            )
        selected = _LexicalJsonTokens(
            tokens.text[start:end],
            location=f"{location}:{label}",
        )
        opening = selected.pop()
        if opening[0] != "punctuation" or opening[3] != "[":
            raise ValueError(
                f"{location}: recognized replay {label} is malformed"
            )
        positions: list[str] = []
        next_token = selected.peek()
        if (
            next_token is not None
            and next_token[0] == "punctuation"
            and next_token[3] == "]"
        ):
            selected.pop()
        else:
            while True:
                value_kind, value_start, value_end, _value = selected.pop()
                if value_kind != "string":
                    raise ValueError(
                        f"{location}: recognized replay {label} contains "
                        "a non-string position"
                    )
                position = _decode_json_string(
                    selected.text,
                    value_start,
                    value_end,
                    location=f"{location}:{label}",
                )
                if not position.strip():
                    raise ValueError(
                        f"{location}: recognized replay {label} contains "
                        "a blank position"
                    )
                positions.append(position)
                separator = selected.pop()
                if (
                    separator[0] == "punctuation"
                    and separator[3] == "]"
                ):
                    break
                if (
                    separator[0] != "punctuation"
                    or separator[3] != ","
                ):
                    raise ValueError(
                        f"{location}: recognized replay {label} has a "
                        "malformed separator"
                    )
        if selected.peek() is not None:
            raise ValueError(
                f"{location}: recognized replay {label} has trailing data"
            )
        if len(positions) > 512:
            raise ValueError(
                f"{location}: recognized replay {label} is too long"
            )
        return positions

    def unique_direct_span(
        direct: Mapping[str, list[tuple[int, int, str]]],
        key: str,
        schema: str,
    ) -> tuple[int, int, str]:
        spans = direct.get(key, [])
        if len(spans) != 1:
            raise ValueError(
                f"{location}: recognized {schema} replay requires "
                f"exactly one direct {key} field"
            )
        return spans[0]

    def array_head_role(
        span: tuple[int, int, str],
    ) -> str:
        start, end, kind = span
        if kind != "array":
            return "non-array"
        selected = _LexicalJsonTokens(
            tokens.text[start:end],
            location=f"{location}:replay-array-shape",
        )
        opening = selected.pop()
        if opening[0] != "punctuation" or opening[3] != "[":
            raise ValueError(
                f"{location}: replay array shape is malformed"
            )
        first = selected.pop()
        if first[0] == "punctuation" and first[3] == "]":
            return "empty"
        if first[0] == "string":
            return "string"
        if first[0] == "punctuation" and first[3] == "{":
            return "structured-object"
        return "other"

    def collect_replay(
        direct: Mapping[str, list[tuple[int, int, str]]],
        *,
        pointer: str,
        ordinal: int,
        opening_entry: bool,
    ) -> None:
        if replay_requests is None:
            return
        record_type: str | None = None
        if "recordtype" in direct:
            record_type = decoded_direct_string(
                unique_direct_span(
                    direct, "recordtype", "event-opening-moves"
                ),
                "recordType",
            ).casefold()
        moves_head = (
            array_head_role(
                unique_direct_span(direct, "moves", "replay-shape")
            )
            if "moves" in direct
            else None
        )
        positions_head = (
            array_head_role(
                unique_direct_span(
                    direct, "positions", "replay-shape"
                )
            )
            if "positions" in direct
            else None
        )
        moves_are_coordinates = moves_head in {"empty", "string"}
        positions_are_ofens = positions_head in {"empty", "string"}
        event_schema = record_type == "gamestart"
        transcript_schema = (
            "initialofen" in direct
            and moves_are_coordinates
            and positions_are_ofens
        )
        schedule_schema = (
            moves_are_coordinates
            and ("initialofen" in direct or opening_entry)
            and not transcript_schema
        )
        if (
            moves_are_coordinates
            and "initialofen" in direct
            and "positions" in direct
            and not positions_are_ofens
        ):
            raise ValueError(
                f"{location}: transcript Positions is not a string array"
            )
        if (
            "moves" in direct
            and ("initialofen" in direct or opening_entry)
            and moves_head not in {
                "empty",
                "string",
                "structured-object",
            }
        ):
            raise ValueError(
                f"{location}: replay Moves is neither a coordinate-string "
                "array nor an opaque structured-object log"
            )
        if (
            moves_are_coordinates
            and "openingmoves" in direct
            and (
                event_schema
                or transcript_schema
                or schedule_schema
                or opening_entry
            )
        ):
            raise ValueError(
                f"{location}: replay object has ambiguous direct move arrays"
            )
        if sum((event_schema, transcript_schema, schedule_schema)) > 1:
            raise ValueError(
                f"{location}: replay object ambiguously matches two schemas"
            )
        if event_schema:
            initial_span = unique_direct_span(
                direct, "initialofen", "event-opening-moves"
            )
            moves_span = unique_direct_span(
                direct, "openingmoves", "event-opening-moves"
            )
            schema = "event-opening-moves"
            initial_source = "explicit"
            container_proof = "event-game-start"
            expected_positions = None
        elif transcript_schema:
            initial_span = unique_direct_span(
                direct, "initialofen", "transcript"
            )
            moves_span = unique_direct_span(
                direct, "moves", "transcript"
            )
            positions_span = unique_direct_span(
                direct, "positions", "transcript"
            )
            schema = "transcript"
            initial_source = "explicit"
            container_proof = "transcript-game"
            expected_positions = decoded_position_array(
                positions_span, "positions"
            )
        elif schedule_schema:
            initial_span = (
                unique_direct_span(
                    direct, "initialofen", "schedule-moves"
                )
                if "initialofen" in direct
                else None
            )
            moves_span = unique_direct_span(
                direct, "moves", "schedule-moves"
            )
            schema = "schedule-moves"
            initial_source = (
                "explicit"
                if initial_span is not None
                else "official-default"
            )
            container_proof = (
                "openings-container"
                if opening_entry
                else "direct-object"
            )
            expected_positions = None
        else:
            return
        moves = decoded_move_array(
            moves_span,
            "openingMoves" if event_schema else "moves",
        )
        if (
            expected_positions is not None
            and len(expected_positions) != len(moves)
        ):
            raise ValueError(
                f"{location}: transcript Moves/Positions count mismatch"
            )
        replay_requests.append(
            {
                "source": location,
                "sourceRecord": source_record,
                "sourceObjectOrdinal": ordinal,
                "sourceObjectIdentity": pointer,
                "schema": schema,
                "initialSource": initial_source,
                "containerProof": container_proof,
                "initialOfen": (
                    decoded_direct_string(
                        initial_span, "initialOfen"
                    )
                    if initial_span is not None
                    else OMEGA_INITIAL_OFEN
                ),
                "moves": moves,
                "expectedPositions": expected_positions,
            }
        )

    def parse_value(
        selected_ofen_key: str | None = None,
        selected_container: bool = False,
        *,
        selected_structural_key: str | None = None,
        pointer: str = "",
        opening_container: bool = False,
        opening_entry: bool = False,
    ) -> tuple[int, int, str]:
        nonlocal object_ordinal
        kind, start, end, value = tokens.pop()
        structural_alias = (
            alias_lookup.get((pointer, selected_structural_key))
            if selected_structural_key is not None
            else None
        )
        if (
            selected_ofen_key is None
            and structural_alias is not None
        ):
            if (
                structural_alias["valueKind"] != "string"
                or kind != "string"
            ):
                raise ValueError(
                    f"{location}: pinned structural container alias is "
                    "not a string"
                )
            _validate_structural_scalar_alias(
                tokens.text,
                start,
                end,
                alias=structural_alias,
                location=location,
            )
            if structural_alias_counts is not None:
                structural_alias_counts[
                    str(structural_alias["role"])
                ] += 1
            if structural_alias_field_counts is not None:
                structural_alias_field_counts[
                    str(structural_alias["field"])
                ] += 1
            return start, end, kind
        if selected_ofen_key is not None:
            if structural_alias is None:
                if kind == "literal" and value == "null":
                    if null_scalar_value_counts is not None:
                        null_scalar_value_counts[selected_ofen_key] += 1
                    return start, end, kind
                if kind != "string":
                    raise ValueError(
                        f"{location}: OFEN-family value is neither a JSON "
                        "string nor null"
                    )
                decoded_ofen = _decode_json_string(
                    tokens.text,
                    start,
                    end,
                    location=location,
                )
                try:
                    canonical_ofen, canonicalized = (
                        _canonicalize_scalar_ofen(
                            selected_ofen_key,
                            decoded_ofen,
                            location=location,
                        )
                    )
                except (TypeError, ValueError) as error:
                    raise ValueError(
                        f"{location}: OFEN-family string is not a valid "
                        "Omega OFEN"
                    ) from error
                if (
                    canonicalized
                    and partial_ofen_canonicalization_counts is not None
                ):
                    partial_ofen_canonicalization_counts[
                        selected_ofen_key
                    ] += 1
                input_arity = len(decoded_ofen.split())
                if scalar_ofen_arity_counts is not None:
                    scalar_ofen_arity_counts[
                        (selected_ofen_key, input_arity)
                    ] += 1
                if (
                    canonicalized
                    and partial_ofen_canonicalization_events is not None
                ):
                    if source_identity is None:
                        raise ValueError(
                            f"{location}: canonicalization event has no "
                            "source identity"
                        )
                    try:
                        event_source = {
                            "path": str(
                                _resolve(
                                    Path(str(source_identity["path"]))
                                )
                            ),
                            "bytes": int(source_identity["bytes"]),
                            "sha256": str(
                                source_identity["sha256"]
                            ).lower(),
                        }
                    except (KeyError, TypeError, ValueError) as error:
                        raise ValueError(
                            f"{location}: canonicalization event source "
                            "identity is malformed"
                        ) from error
                    if (
                        event_source["bytes"] < 0
                        or re.fullmatch(
                            r"[0-9a-f]{64}",
                            str(event_source["sha256"]),
                        )
                        is None
                    ):
                        raise ValueError(
                            f"{location}: canonicalization event source "
                            "identity is malformed"
                        )
                    partial_ofen_canonicalization_events.append(
                        {
                            "source": event_source,
                            "sourceRecord": source_record,
                            "jsonPointer": pointer,
                            "field": selected_ofen_key,
                            "input": _content_identity(
                                decoded_ofen.encode("utf-8")
                            ),
                            "output": _content_identity(
                                canonical_ofen.encode("utf-8")
                            ),
                            "inputFieldCount": input_arity,
                            "outputFieldCount": len(
                                canonical_ofen.split()
                            ),
                            "suffix": list(
                                PARTIAL_OFEN_CANONICAL_SUFFIX
                            ),
                        }
                    )
                found.append(canonical_ofen)
                return start, end, kind
            if structural_alias["valueKind"] == "string":
                if kind != "string":
                    raise ValueError(
                        f"{location}: pinned structural scalar alias is "
                        "not a string"
                    )
                _validate_structural_scalar_alias(
                    tokens.text,
                    start,
                    end,
                    alias=structural_alias,
                    location=location,
                )
                if structural_alias_counts is not None:
                    structural_alias_counts[
                        str(structural_alias["role"])
                    ] += 1
                if structural_alias_field_counts is not None:
                    structural_alias_field_counts[
                        str(structural_alias["field"])
                    ] += 1
                return start, end, kind
            if kind != "punctuation" or value != "{":
                raise ValueError(
                    f"{location}: pinned structural scalar alias is not "
                    "an object"
                )
        if selected_container and kind == "string":
            decoded_container_ofen = _decode_json_string(
                tokens.text,
                start,
                end,
                location=location,
            )
            try:
                canonical_container_ofen, _canonicalized = (
                    _canonicalize_scalar_ofen(
                        "selected-container",
                        decoded_container_ofen,
                        location=location,
                    )
                )
            except (TypeError, ValueError) as error:
                raise ValueError(
                    f"{location}: selected container string is not a "
                    "valid six-field Omega OFEN"
                ) from error
            found.append(canonical_container_ofen)
            return start, end, kind
        if kind in {"string", "number", "literal"}:
            if (
                kind == "string"
                and _raw_string_has_omega_ofen_shape(
                    tokens.text, start, end
                )
            ):
                raise ValueError(
                    f"{location}: skipped ordinary string has an "
                    "unclassified Omega-OFEN-like raw shape"
                )
            return start, end, kind
        if kind != "punctuation":
            raise ValueError(f"{location}: expected a JSON value")
        if value == "{":
            object_ordinal += 1
            current_object_ordinal = object_ordinal
            direct: dict[str, list[tuple[int, int, str]]] = {}
            seen_keys: set[str] = set()
            next_token = tokens.peek()
            if (
                next_token is not None
                and next_token[0] == "punctuation"
                and next_token[3] == "}"
            ):
                closing = tokens.pop()
                if structural_alias is not None:
                    _validate_structural_scalar_alias(
                        tokens.text,
                        start,
                        closing[2],
                        alias=structural_alias,
                        location=location,
                    )
                    if structural_alias_counts is not None:
                        structural_alias_counts[
                            str(structural_alias["role"])
                        ] += 1
                    if structural_alias_field_counts is not None:
                        structural_alias_field_counts[
                            str(structural_alias["field"])
                        ] += 1
                return start, closing[2], "object"
            while True:
                key_kind, key_start, key_end, _key_token = tokens.pop()
                if key_kind != "string":
                    raise ValueError(
                        f"{location}: JSON object key is not a string"
                    )
                punctuation(":")
                key, original_key, role = key_name_and_role(
                    key_start, key_end
                )
                if key in seen_keys:
                    raise ValueError(
                        f"{location}: duplicate case-folded object key "
                        f"{key!r}"
                    )
                seen_keys.add(key)
                escaped_pointer_key = (
                    original_key.replace("~", "~0").replace("/", "~1")
                )
                child_span = parse_value(
                    selected_ofen_key=key if role == "ofen" else None,
                    selected_container=role == "position-container",
                    selected_structural_key=(
                        key
                        if role in {"ofen", "position-container"}
                        else None
                    ),
                    pointer=f"{pointer}/{escaped_pointer_key}",
                    opening_container=key == "openings",
                )
                if key in {
                    "initialofen",
                    "moves",
                    "openingmoves",
                    "positions",
                    "recordtype",
                }:
                    direct.setdefault(key, []).append(child_span)
                separator = tokens.pop()
                if (
                    separator[0] == "punctuation"
                    and separator[3] == "}"
                ):
                    collect_replay(
                        direct,
                        pointer=pointer,
                        ordinal=current_object_ordinal,
                        opening_entry=opening_entry,
                    )
                    if structural_alias is not None:
                        _validate_structural_scalar_alias(
                            tokens.text,
                            start,
                            separator[2],
                            alias=structural_alias,
                            location=location,
                        )
                        if structural_alias_counts is not None:
                            structural_alias_counts[
                                str(structural_alias["role"])
                            ] += 1
                        if structural_alias_field_counts is not None:
                            structural_alias_field_counts[
                                str(structural_alias["field"])
                            ] += 1
                    return start, separator[2], "object"
                if (
                    separator[0] != "punctuation"
                    or separator[3] != ","
                ):
                    raise ValueError(
                        f"{location}: malformed JSON object separator"
                    )
        if value == "[":
            next_token = tokens.peek()
            if (
                next_token is not None
                and next_token[0] == "punctuation"
                and next_token[3] == "]"
            ):
                closing = tokens.pop()
                return start, closing[2], "array"
            array_index = 0
            while True:
                parse_value(
                    selected_container=selected_container,
                    pointer=f"{pointer}/{array_index}",
                    opening_entry=opening_container,
                )
                array_index += 1
                separator = tokens.pop()
                if (
                    separator[0] == "punctuation"
                    and separator[3] == "]"
                ):
                    return start, separator[2], "array"
                if (
                    separator[0] != "punctuation"
                    or separator[3] != ","
                ):
                    raise ValueError(
                        f"{location}: malformed JSON array separator"
                    )
        raise ValueError(
            f"{location}: punctuation {value!r} cannot begin a value"
        )

    try:
        parse_value()
    except RecursionError as error:
        raise ValueError(f"{location}: JSON nesting is too deep") from error
    if tokens.peek() is not None:
        raise ValueError(f"{location}: trailing content after JSON value")
    return found, position_like_key_counts


def _lexical_ofens_validated(
    text: str, *, location: str
) -> list[str]:
    return _lexical_scan_validated(text, location=location)[0]


def _normalize_full_ofen(value: str, *, label: str) -> str:
    fields = value.split()
    if len(fields) != 6:
        raise ValueError(
            f"{label} must have exactly six whitespace-delimited fields"
        )
    return " ".join(fields)


def _strict_object_pairs(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    value: dict[str, Any] = {}
    seen: set[str] = set()
    for key, child in pairs:
        folded = key.casefold()
        if folded in seen:
            raise ValueError(
                f"duplicate case-folded generated JSON field {folded!r}"
            )
        seen.add(folded)
        value[key] = child
    return value


def _application_runtime_bundle_identity(
    expected: Mapping[str, Any],
) -> dict[str, Any]:
    root, regular_files = _regular_tree_files(
        Path(str(expected["path"])),
        label="app-local runtime bundle",
    )
    assembly_relative = str(expected["assemblyRelativePath"])
    files: list[dict[str, Any]] = []
    canonical = bytearray()
    for path in regular_files:
        identity = _identity(path)
        relative = path.relative_to(root).as_posix()
        files.append(
            {
                "relativePath": relative,
                "bytes": identity["bytes"],
                "sha256": identity["sha256"],
            }
        )
        canonical.extend(
            (
                f"{relative}\t{identity['bytes']}\t"
                f"{identity['sha256']}\n"
            ).encode("utf-8")
        )
    if not any(
        item["relativePath"] == assembly_relative for item in files
    ):
        raise ValueError(
            f"{assembly_relative} is absent from its app-local runtime "
            "bundle"
        )
    return {
        "path": str(root),
        "assemblyRelativePath": assembly_relative,
        "fileCount": len(files),
        "bytes": sum(int(item["bytes"]) for item in files),
        "sha256": hashlib.sha256(canonical).hexdigest(),
    }


def _directory_bundle_identity(
    expected: Mapping[str, Any],
) -> dict[str, Any]:
    root, regular_files = _regular_tree_files(
        Path(str(expected["path"])),
        label="runtime selection namespace",
    )
    files: list[dict[str, Any]] = []
    canonical = bytearray()
    for path in regular_files:
        identity = _identity(path)
        relative = path.relative_to(root).as_posix()
        files.append(
            {
                "relativePath": relative,
                "bytes": identity["bytes"],
                "sha256": identity["sha256"],
            }
        )
        canonical.extend(
            (
                f"{relative}\t{identity['bytes']}\t"
                f"{identity['sha256']}\n"
            ).encode("utf-8")
        )
    result: dict[str, Any] = {
        "path": str(root),
        "fileCount": len(files),
        "bytes": sum(int(item["bytes"]) for item in files),
        "sha256": hashlib.sha256(canonical).hexdigest(),
    }
    if "versionDirectories" in expected:
        version_directories = []
        for item in root.iterdir():
            if _is_reparse_or_symlink(item):
                raise ValueError(
                    "runtime selection namespace contains a "
                    f"reparse/symlink entry: {item}"
                )
            if stat.S_ISDIR(item.lstat().st_mode):
                version_directories.append(item.name)
        result["versionDirectories"] = sorted(
            version_directories, key=str.casefold
        )
    return result


def _is_reparse_or_symlink(path: Path) -> bool:
    metadata = path.lstat()
    attributes = int(getattr(metadata, "st_file_attributes", 0))
    reparse_flag = int(
        getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )
    return path.is_symlink() or bool(attributes & reparse_flag)


def _regular_tree_files(
    declared_root: Path,
    *,
    label: str,
) -> tuple[Path, list[Path]]:
    declared_root = declared_root.expanduser()
    if not declared_root.is_absolute():
        declared_root = Path.cwd() / declared_root
    if (
        not declared_root.exists()
        or not declared_root.is_dir()
        or _is_reparse_or_symlink(declared_root)
    ):
        raise ValueError(
            f"{label} root is absent, not a directory, or is a "
            f"reparse/symlink: {declared_root}"
        )
    root = declared_root.resolve(strict=True)
    files: list[Path] = []
    for item in root.rglob("*"):
        if _is_reparse_or_symlink(item):
            raise ValueError(
                f"{label} contains a reparse/symlink entry: {item}"
            )
        mode = item.lstat().st_mode
        if stat.S_ISDIR(mode):
            continue
        if not stat.S_ISREG(mode):
            raise ValueError(
                f"{label} contains a non-regular entry: {item}"
            )
        files.append(item)
    files.sort(key=lambda item: item.relative_to(root).as_posix())
    return root, files


def _require_absent_runtime_paths(
    paths: Mapping[str, Path] = DOTNET_REQUIRED_ABSENT_PATHS,
) -> dict[str, str]:
    absent_paths: dict[str, str] = {}
    for name, path in paths.items():
        path = path.expanduser()
        if os.path.lexists(path):
            raise ValueError(
                f".NET runtime path required to remain absent exists: "
                f"{name}={path}"
            )
        absent_paths[name] = str(path)
    return absent_paths


def _verify_dotnet_runtime() -> dict[str, Any]:
    verified: dict[str, Any] = {}
    absent_paths = _require_absent_runtime_paths()
    _verify_identity(DOTNET_RUNTIME_PINS["host"], ".NET host")
    verified["host"] = _identity(
        Path(str(DOTNET_RUNTIME_PINS["host"]["path"]))
    )
    for key, expected in DOTNET_RUNTIME_PINS.items():
        if key == "host":
            continue
        actual = _directory_bundle_identity(expected)
        _expect(actual, expected, f".NET {key}")
        verified[key] = actual
    verified["requiredAbsentPaths"] = absent_paths
    return verified


def _dotnet_environment() -> dict[str, str]:
    _verify_dotnet_runtime()
    prefixes = tuple(
        value.casefold()
        for value in DOTNET_ENVIRONMENT_POLICY[
            "clearedAmbientPrefixesCaseInsensitive"
        ]
    )
    cleared_names = frozenset(
        value.casefold()
        for value in DOTNET_ENVIRONMENT_POLICY[
            "clearedAmbientNamesCaseInsensitive"
        ]
    )
    environment = {
        key: value
        for key, value in os.environ.items()
        if (
            not key.casefold().startswith(prefixes)
            and key.casefold() not in cleared_names
        )
    }
    set_variables = DOTNET_ENVIRONMENT_POLICY["setVariables"]
    if not isinstance(set_variables, dict):
        raise ValueError("managed subprocess environment is malformed")
    environment.update(
        {str(key): str(value) for key, value in set_variables.items()}
    )
    expected_root = _resolve(DOTNET.parent)
    if _resolve(Path(environment["DOTNET_ROOT"])) != expected_root:
        raise ValueError("managed subprocess DOTNET_ROOT changed")
    return environment


def _run_pinned_dotnet(
    command: Sequence[str | os.PathLike[str]],
    *,
    runtime_bundle_pin: Mapping[str, Any],
    **kwargs: Any,
) -> subprocess.CompletedProcess[Any]:
    command = [str(value) for value in command]
    if len(command) < 2:
        raise ValueError("managed subprocess command is incomplete")
    if _resolve(Path(command[0])) != _resolve(DOTNET):
        raise ValueError("managed subprocess changed the pinned .NET host")
    runtime_root = _resolve(
        Path(str(runtime_bundle_pin["path"]))
    )
    expected_assembly = runtime_root / str(
        runtime_bundle_pin["assemblyRelativePath"]
    )
    if _resolve(Path(command[1])) != _resolve(expected_assembly):
        raise ValueError(
            "managed subprocess changed its pinned app-local assembly"
        )
    if "env" in kwargs:
        raise ValueError(
            "managed subprocess callers cannot supply an ambient environment"
        )
    _expect(
        _application_runtime_bundle_identity(runtime_bundle_pin),
        runtime_bundle_pin,
        "managed subprocess app-local runtime bundle",
    )
    kwargs["env"] = _dotnet_environment()
    try:
        return subprocess.run(command, **kwargs)
    finally:
        _expect(
            _application_runtime_bundle_identity(runtime_bundle_pin),
            runtime_bundle_pin,
            "managed subprocess post-run app-local runtime bundle",
        )
        _verify_dotnet_runtime()


def _opening_replay_runtime_bundle_identity() -> dict[str, Any]:
    return _application_runtime_bundle_identity(
        OPENING_REPLAY_RUNTIME_BUNDLE_PIN
    )


def _verify_prebuilt_tooling() -> dict[str, Any]:
    result: dict[str, Any] = {}
    for label, pins in (
        ("rootSampler", ROOT_SAMPLER_PINS),
        ("historySnapshot", HISTORY_SNAPSHOT_PINS),
        ("omegaMatch", OMEGA_MATCH_PINS),
    ):
        verified: dict[str, Any] = {}
        for key, expected in pins.items():
            if key == "appLocalRuntimeBundle":
                actual = _application_runtime_bundle_identity(expected)
                _expect(
                    actual,
                    expected,
                    f"{label} app-local runtime bundle",
                )
                verified[key] = actual
            else:
                _verify_identity(expected, f"{label} {key}")
                verified[key] = _identity(
                    Path(str(expected["path"]))
                )
        result[label] = verified
    result.update(_verify_dotnet_runtime())
    return result


def _opening_replay_identity_bundle() -> dict[str, dict[str, Any]]:
    bundle: dict[str, dict[str, Any]] = {}
    for key, expected in OPENING_REPLAY_PINS.items():
        _verify_identity(expected, f"opening replay {key}")
        bundle[key] = _identity(Path(str(expected["path"])))
    bundle["appLocalRuntimeBundle"] = (
        _opening_replay_runtime_bundle_identity()
    )
    return bundle


def _projection_bytes(
    requests: Sequence[Mapping[str, Any]],
) -> bytes:
    return (
        "".join(
            json.dumps(
                dict(request),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
            for request in requests
        )
    ).encode("utf-8")


def _content_identity(payload: bytes) -> dict[str, Any]:
    return {
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _decimal_evidence(value: Any) -> str:
    if isinstance(value, bool):
        raise TypeError("boolean cannot be an evidence count")
    number = int(value)
    if number < 0 or number >= 10**20:
        raise ValueError("evidence count exceeds its fixed-width envelope")
    return f"{number:020d}"


def _content_identity_evidence(
    value: Mapping[str, Any],
) -> dict[str, str]:
    sha256 = str(value.get("sha256", "")).lower()
    if not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise ValueError("evidence content identity has an invalid sha256")
    return {
        "bytesDecimal": _decimal_evidence(value.get("bytes")),
        "sha256": sha256,
    }


def _count_evidence(
    counts: Mapping[str, Any],
) -> list[dict[str, str]]:
    return [
        {
            "field": str(key),
            "occurrencesDecimal": _decimal_evidence(counts[key]),
        }
        for key in sorted(counts)
    ]


def _final_target_free_inventory_summary(
    *,
    signatures: set[str],
    pins: Sequence[Mapping[str, Any]],
    positions: int,
    position_like_key_counts: Mapping[str, int],
    stable_artifact_count: int,
    replay_audit: Mapping[str, Any],
    complement: Mapping[str, Any],
) -> dict[str, Any]:
    if replay_audit.get("transcriptFullSixFieldParityPassed") is not True:
        raise ValueError("transcript parity did not pass")
    if (
        replay_audit.get(
            "legacyFourFieldBoardKeyReconciliation", {}
        ).get("passed")
        is not True
    ):
        raise ValueError(
            "legacy four-field board-key reconciliation did not pass"
        )
    return {
        "schemaVersion": 1,
        "kind": "omega-nnue-king-state-v3-final-target-free-inventory",
        "authoritativeUnion": {
            "artifactCountDecimal": _decimal_evidence(len(pins)),
            "artifactBytesDecimal": _decimal_evidence(
                sum(int(pin["bytes"]) for pin in pins)
            ),
            "stableArtifactCountDecimal": _decimal_evidence(
                stable_artifact_count
            ),
        },
        "boardStateEvidence": {
            "rawDecodedOccurrenceCountDecimal": _decimal_evidence(
                replay_audit["rawDecodedPositionOccurrenceCount"]
            ),
            "newlyImplicitOpeningOccurrenceCountDecimal": (
                _decimal_evidence(
                    replay_audit[
                        "newlyImplicitOpeningOccurrenceCount"
                    ]
                )
            ),
            "aggregateExclusionOccurrenceCountDecimal": (
                _decimal_evidence(positions)
            ),
            "rawDecodedNormalizedCardinalityDecimal": (
                _decimal_evidence(
                    replay_audit[
                        "rawDecodedNormalizedOfenCardinality"
                    ]
                )
            ),
            "generatedReplayNormalizedCardinalityDecimal": (
                _decimal_evidence(
                    replay_audit[
                        "generatedReplayNormalizedOfenCardinality"
                    ]
                )
            ),
            "aggregateNormalizedCardinalityDecimal": (
                _decimal_evidence(
                    replay_audit["aggregateNormalizedOfenCardinality"]
                )
            ),
            "rawDecodedConservativeSignatureCardinalityDecimal": (
                _decimal_evidence(
                    replay_audit[
                        "rawDecodedConservativeSignatureCardinality"
                    ]
                )
            ),
            "generatedReplayConservativeSignatureCardinalityDecimal": (
                _decimal_evidence(
                    replay_audit[
                        "generatedReplayConservativeSignatureCardinality"
                    ]
                )
            ),
            "aggregateConservativeSignatureCardinalityDecimal": (
                _decimal_evidence(len(signatures))
            ),
        },
        "triggerKeyCounts": _count_evidence(
            position_like_key_counts
        ),
        "nullScalarCounts": _count_evidence(
            replay_audit["nullScalarValueCounts"]
        ),
        "legacyFourFieldBoardKeyCounts": _count_evidence(
            replay_audit["partialOfenCanonicalizationCounts"]
        ),
        "legacyFourFieldBoardKeyEvidence": {
            "countDecimal": _decimal_evidence(
                replay_audit["partialOfenCanonicalizationCount"]
            ),
            "arityCountEntries": [
                {
                    "field": str(entry["field"]),
                    "inputFieldCount": int(
                        entry["inputFieldCount"]
                    ),
                    "occurrencesDecimal": _decimal_evidence(
                        entry["occurrences"]
                    ),
                }
                for entry in replay_audit[
                    "legacyFourFieldBoardKeyArityCountEntries"
                ]
            ],
            "sourceEntries": [
                {
                    "workspaceRelativePath": str(
                        entry["workspaceRelativePath"]
                    ),
                    "bytesDecimal": _decimal_evidence(entry["bytes"]),
                    "sha256": str(entry["sha256"]),
                    "occurrencesDecimal": _decimal_evidence(
                        entry["occurrences"]
                    ),
                }
                for entry in replay_audit[
                    "legacyFourFieldBoardKeySourceEntries"
                ]
            ],
            "orderedExpansionEventProjection": (
                _content_identity_evidence(
                    replay_audit[
                        "legacyFourFieldBoardKeyProjection"
                    ]
                )
            ),
            "reconciliation": {
                "field": str(
                    replay_audit[
                        "legacyFourFieldBoardKeyReconciliation"
                    ]["field"]
                ),
                "triggerOccurrencesDecimal": _decimal_evidence(
                    replay_audit[
                        "legacyFourFieldBoardKeyReconciliation"
                    ]["triggerOccurrences"]
                ),
                "acceptedScalarOccurrencesDecimal": _decimal_evidence(
                    replay_audit[
                        "legacyFourFieldBoardKeyReconciliation"
                    ]["acceptedScalarOccurrences"]
                ),
                "nullOccurrencesDecimal": _decimal_evidence(
                    replay_audit[
                        "legacyFourFieldBoardKeyReconciliation"
                    ]["nullOccurrences"]
                ),
                "structuralAliasOccurrencesDecimal": _decimal_evidence(
                    replay_audit[
                        "legacyFourFieldBoardKeyReconciliation"
                    ]["structuralAliasOccurrences"]
                ),
                "passed": (
                    replay_audit[
                        "legacyFourFieldBoardKeyReconciliation"
                    ]["passed"]
                    is True
                ),
            },
        },
        "structuralScalarAliases": _count_evidence(
            replay_audit["structuralScalarAliasCounts"]
        ),
        "replayEvidence": {
            "requestCountDecimal": _decimal_evidence(
                replay_audit["requestCount"]
            ),
            "prefixRecordCountDecimal": _decimal_evidence(
                replay_audit["prefixRecordCount"]
            ),
            "openingRequestCountDecimal": _decimal_evidence(
                replay_audit["openingRequestCount"]
            ),
            "openingPrefixRecordCountDecimal": _decimal_evidence(
                replay_audit["openingPrefixRecordCount"]
            ),
            "transcriptRequestCountDecimal": _decimal_evidence(
                replay_audit["transcriptRequestCount"]
            ),
            "transcriptPrefixRecordCountDecimal": _decimal_evidence(
                replay_audit["transcriptPrefixRecordCount"]
            ),
            "uniqueOpeningPrefixIdentityCountDecimal": (
                _decimal_evidence(
                    replay_audit["uniqueOpeningPrefixIdentityCount"]
                )
            ),
            "uniqueOpeningPostMovePrefixIdentityCountDecimal": (
                _decimal_evidence(
                    replay_audit[
                        "uniqueOpeningPostMovePrefixIdentityCount"
                    ]
                )
            ),
            "fullParityMismatchCountDecimal": _decimal_evidence(0),
            "parseOrReplayErrorCountDecimal": _decimal_evidence(
                replay_audit["parseOrReplayErrors"]
            ),
            "projectionIdentity": _content_identity_evidence(
                replay_audit["projection"]
            ),
            "outputIdentity": _content_identity_evidence(
                replay_audit["output"]
            ),
        },
        "complementEvidence": {
            "artifactCountDecimal": _decimal_evidence(
                complement["artifactCount"]
            ),
            "artifactBytesDecimal": _decimal_evidence(
                complement["artifactBytes"]
            ),
            "orderedIdentitySet": _content_identity_evidence(
                complement["orderedIdentitySet"]
            ),
            "classifiedHitArtifactCountDecimal": _decimal_evidence(
                complement["classifiedHitArtifactCount"]
            ),
            "classifiedHitOrderedIdentitySet": (
                _content_identity_evidence(
                    complement["classifiedHitOrderedIdentitySet"]
                )
            ),
            "triggerKeyCounts": _count_evidence(
                complement["triggerKeyHits"]
            ),
            "rawBoardStringShapeHitCountDecimal": _decimal_evidence(
                complement["rawBoardStringShapeHits"]
            ),
            "unclassifiedHitArtifactCountDecimal": _decimal_evidence(
                complement["unclassifiedHitArtifactCount"]
            ),
        },
        "informationBoundary": {
            "targetFieldsDecoded": 0,
            "targetFieldsEmitted": 0,
            "historicalEvidenceOnly": True,
        },
    }


def _replay_opening_prefixes(
    requests: Sequence[Mapping[str, Any]],
    *,
    progress: ProgressCallback | None = None,
) -> tuple[list[str], dict[str, Any]]:
    if not requests:
        raise ValueError(
            "known historical evidence produced an empty replay projection"
        )
    _emit_progress(
        progress,
        "opening-replay-start",
        requestCount=len(requests),
    )
    runtime = _opening_replay_identity_bundle()
    expected_request_fields = {
        "schemaVersion",
        "kind",
        "requestId",
        "sourcePath",
        "sourceBytes",
        "sourceSha256",
        "sourceRecord",
        "sourceObjectOrdinal",
        "sourceObjectIdentity",
        "schema",
        "initialSource",
        "containerProof",
        "initialOfen",
        "moves",
        "expectedPositions",
    }
    source_objects: set[tuple[str, int, int, str]] = set()
    schema_counts: Counter[str] = Counter()
    initial_source_counts: Counter[str] = Counter()
    source_identities: dict[str, dict[str, Any]] = {}
    opening_sequences: set[tuple[str, tuple[str, ...]]] = set()
    transcript_moves = 0
    for expected_id, raw_request in enumerate(requests):
        request = dict(raw_request)
        if set(request) != expected_request_fields:
            raise ValueError(
                f"replay request {expected_id} field inventory changed"
            )
        if (
            request["schemaVersion"] != 1
            or request["kind"] != OPENING_REPLAY_REQUEST_KIND
            or request["requestId"] != expected_id
        ):
            raise ValueError(
                f"replay request {expected_id} identity changed"
            )
        source_path = str(_resolve(Path(str(request["sourcePath"]))))
        source_identity = {
            "path": source_path,
            "bytes": int(request["sourceBytes"]),
            "sha256": str(request["sourceSha256"]),
        }
        prior_source = source_identities.get(source_path.casefold())
        if prior_source is None:
            _verify_identity(
                source_identity,
                f"replay request {expected_id} source",
            )
            source_identities[source_path.casefold()] = source_identity
        elif prior_source != source_identity:
            raise ValueError(
                f"replay source identity conflicts for {source_path}"
            )
        source_object = (
            source_path.casefold(),
            int(request["sourceRecord"]),
            int(request["sourceObjectOrdinal"]),
            str(request["sourceObjectIdentity"]),
        )
        if source_object in source_objects:
            raise ValueError(
                f"duplicate replay source-record/object identity: "
                f"{source_object!r}"
            )
        source_objects.add(source_object)
        schema = str(request["schema"])
        if schema not in {
            "schedule-moves",
            "event-opening-moves",
            "transcript",
        }:
            raise ValueError(
                f"replay request {expected_id} has an unknown schema"
            )
        moves = request["moves"]
        if (
            not isinstance(moves, list)
            or len(moves) > 512
            or any(
                not isinstance(move, str)
                or not move
                or move != move.strip()
                for move in moves
            )
        ):
            raise ValueError(
                f"replay request {expected_id} has invalid moves"
            )
        expected_positions = request["expectedPositions"]
        if schema == "transcript":
            if (
                not isinstance(expected_positions, list)
                or len(expected_positions) != len(moves)
                or any(
                    not isinstance(position, str)
                    or not position.strip()
                    for position in expected_positions
                )
            ):
                raise ValueError(
                    f"replay request {expected_id} has invalid "
                    "transcript positions"
                )
            transcript_moves += len(moves)
        elif expected_positions is not None:
            raise ValueError(
                f"replay request {expected_id} unexpectedly exposes "
                "positions"
            )
        normalized_initial = _normalize_full_ofen(
            str(request["initialOfen"]),
            label=f"replay request {expected_id} initial OFEN",
        )
        if (
            request["initialSource"] == "official-default"
            and (
                schema != "schedule-moves"
                or request["containerProof"] != "openings-container"
                or normalized_initial != OMEGA_INITIAL_OFEN
            )
        ):
            raise ValueError(
                f"replay request {expected_id} has an unproved default"
            )
        schema_counts[schema] += 1
        initial_source_counts[str(request["initialSource"])] += 1
        if schema != "transcript":
            opening_sequences.add(
                (
                    normalized_initial,
                    tuple(str(move).casefold() for move in moves),
                )
            )

    projection_payload = _projection_bytes(requests)
    with tempfile.TemporaryDirectory(
        prefix="omega-opening-prefix-replay-"
    ) as temporary:
        directory = Path(temporary)
        projection = directory / "projection.jsonl"
        output = directory / "prefixes.jsonl"
        manifest_path = directory / "manifest.json"
        _exclusive_bytes(projection, projection_payload)
        try:
            completed = _run_pinned_dotnet(
                [
                    str(DOTNET),
                    str(OPENING_REPLAY_HELPER),
                    "--input",
                    str(projection),
                    "--output",
                    str(output),
                    "--manifest",
                    str(manifest_path),
                ],
                runtime_bundle_pin=OPENING_REPLAY_RUNTIME_BUNDLE_PIN,
                cwd=REPO,
                text=True,
                encoding="utf-8",
                errors="strict",
                capture_output=True,
                check=False,
                timeout=OPENING_REPLAY_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as error:
            raise ValueError(
                "opening replay helper exceeded the fixed "
                f"{OPENING_REPLAY_TIMEOUT_SECONDS}-second timeout"
            ) from error
        if completed.returncode != 0:
            raise ValueError(
                "opening replay helper rejected the target-free "
                f"projection: exit={completed.returncode}; "
                f"stderr={completed.stderr.strip()!r}"
            )
        if not output.is_file() or not manifest_path.is_file():
            raise ValueError(
                "opening replay helper did not publish both no-clobber "
                "artifacts"
            )
        output_payload = output.read_bytes()
        manifest_payload = manifest_path.read_bytes()
        manifest = json.loads(
            manifest_payload.decode("utf-8"),
            object_pairs_hook=_strict_object_pairs,
        )
        if not isinstance(manifest, dict) or set(manifest) != {
            "schemaVersion",
            "kind",
            "variant",
            "rules",
            "input",
            "sourceSet",
            "sourceCount",
            "requests",
            "prefixes",
            "parseOrReplayErrors",
            "output",
            "runtime",
        }:
            raise ValueError("opening replay manifest field inventory changed")
        if (
            manifest["schemaVersion"] != 1
            or manifest["kind"] != OPENING_REPLAY_MANIFEST_KIND
            or manifest["variant"] != "Omega"
            or manifest["rules"] != "ChessLib legal coordinate replay"
            or manifest["requests"] != len(requests)
            or manifest["parseOrReplayErrors"] != 0
        ):
            raise ValueError("opening replay manifest contract changed")
        if not _same_identity(manifest["input"], _identity(projection)):
            raise ValueError("opening replay manifest input linkage changed")
        if not _same_identity(manifest["output"], _identity(output)):
            raise ValueError("opening replay manifest output linkage changed")
        manifest_runtime = manifest["runtime"]
        if (
            not isinstance(manifest_runtime, dict)
            or set(manifest_runtime) != {"helper", "rules"}
            or not _same_identity(
                manifest_runtime["helper"], runtime["helper"]
            )
            or not _same_identity(
                manifest_runtime["rules"], runtime["rulesAssembly"]
            )
        ):
            raise ValueError("opening replay runtime linkage changed")
        source_set = manifest["sourceSet"]
        if (
            not isinstance(source_set, list)
            or manifest["sourceCount"] != len(source_identities)
            or len(source_set) != len(source_identities)
        ):
            raise ValueError("opening replay source-set count changed")
        observed_source_keys: set[str] = set()
        for source in source_set:
            if not isinstance(source, dict):
                raise ValueError("opening replay source identity is invalid")
            source_key = str(source.get("path", "")).casefold()
            if source_key in observed_source_keys:
                raise ValueError(
                    "opening replay source-set contains a duplicate source"
                )
            observed_source_keys.add(source_key)
            expected_source = source_identities.get(source_key)
            if (
                expected_source is None
                or not _same_identity(source, expected_source)
            ):
                raise ValueError(
                    "opening replay source-set linkage changed"
                )
        if observed_source_keys != set(source_identities):
            raise ValueError(
                "opening replay source-set is not an exact source bijection"
            )

        expected_prefix_fields = {
            "schemaVersion",
            "kind",
            "requestId",
            "sourcePath",
            "sourceBytes",
            "sourceSha256",
            "sourceRecord",
            "sourceObjectOrdinal",
            "sourceObjectIdentity",
            "schema",
            "initialSource",
            "containerProof",
            "ply",
            "move",
            "ofen",
        }
        replay_ofens: list[str] = []
        opening_replay_normalized: set[str] = set()
        transcript_replay_normalized: set[str] = set()
        replay_rows: list[dict[str, Any]] = []
        for line_number, line in enumerate(
            output_payload.decode("utf-8").splitlines(), 1
        ):
            if not line.strip():
                raise ValueError(
                    "opening replay output contains a blank record"
                )
            row = json.loads(
                line, object_pairs_hook=_strict_object_pairs
            )
            if not isinstance(row, dict) or set(row) != expected_prefix_fields:
                raise ValueError(
                    f"opening replay row {line_number} fields changed"
                )
            replay_rows.append(row)
        expected_prefixes = sum(
            len(request["moves"]) + 1 for request in requests
        )
        if (
            len(replay_rows) != expected_prefixes
            or manifest["prefixes"] != expected_prefixes
        ):
            raise ValueError("opening replay prefix count changed")
        cursor = 0
        opening_prefix_records = 0
        transcript_prefix_records = 0
        for request_id, request in enumerate(requests):
            request_rows = replay_rows[
                cursor : cursor + len(request["moves"]) + 1
            ]
            cursor += len(request_rows)
            for ply, row in enumerate(request_rows):
                for field in (
                    "sourcePath",
                    "sourceBytes",
                    "sourceSha256",
                    "sourceRecord",
                    "sourceObjectOrdinal",
                    "sourceObjectIdentity",
                    "schema",
                    "initialSource",
                    "containerProof",
                ):
                    if row[field] != request[field]:
                        raise ValueError(
                            f"opening replay request {request_id} source "
                            f"linkage changed for {field}"
                        )
                expected_move = (
                    None if ply == 0 else request["moves"][ply - 1]
                )
                if (
                    row["schemaVersion"] != 1
                    or row["kind"] != OPENING_REPLAY_PREFIX_KIND
                    or row["requestId"] != request_id
                    or row["ply"] != ply
                    or row["move"] != expected_move
                    or not isinstance(row["ofen"], str)
                ):
                    raise ValueError(
                        f"opening replay request {request_id} prefix "
                        f"{ply} changed"
                    )
                normalized = _normalize_full_ofen(
                    row["ofen"],
                    label=(
                        f"opening replay request {request_id} "
                        f"prefix {ply}"
                    ),
                )
                if ply == 0 and normalized != _normalize_full_ofen(
                    str(request["initialOfen"]),
                    label=f"replay request {request_id} initial OFEN",
                ):
                    raise ValueError(
                        f"opening replay request {request_id} omitted or "
                        "changed ply zero"
                    )
                if (
                    request["schema"] == "transcript"
                    and ply > 0
                    and normalized
                    != _normalize_full_ofen(
                        request["expectedPositions"][ply - 1],
                        label=(
                            f"replay request {request_id} expected "
                            f"position {ply}"
                        ),
                    )
                ):
                    raise ValueError(
                        f"opening replay request {request_id} transcript "
                        f"parity changed at ply {ply}"
                    )
                replay_ofens.append(normalized)
                if request["schema"] == "transcript":
                    transcript_replay_normalized.add(normalized)
                else:
                    opening_replay_normalized.add(normalized)
            if request["schema"] == "transcript":
                transcript_prefix_records += len(request_rows)
            else:
                opening_prefix_records += len(request_rows)
        if cursor != len(replay_rows):
            raise ValueError("opening replay output grouping changed")
        runtime_after_replay = _opening_replay_identity_bundle()
        if runtime_after_replay != runtime:
            raise ValueError(
                "opening replay implementation or runtime changed while "
                "the projection was replayed and verified"
            )
        _emit_progress(
            progress,
            "opening-replay-source-rehash-start",
            sourceArtifactCount=len(source_identities),
        )
        for source_key in sorted(source_identities):
            _verify_identity(
                source_identities[source_key],
                "post-replay source",
            )
        _emit_progress(
            progress,
            "opening-replay-source-rehash-complete",
            sourceArtifactCount=len(source_identities),
        )

    unique_length_counts = Counter(
        len(moves) for _initial, moves in opening_sequences
    )
    unique_opening_prefix_records = sum(
        len(moves) + 1 for _initial, moves in opening_sequences
    )
    opening_prefix_identities = {
        (initial, moves[:ply])
        for initial, moves in opening_sequences
        for ply in range(len(moves) + 1)
    }
    opening_postmove_prefix_identities = {
        identity
        for identity in opening_prefix_identities
        if identity[1]
    }
    audit = {
        "schemaVersion": 1,
        "kind": "omega-opening-prefix-replay-audit-v1",
        "variant": "Omega",
        "rules": "ChessLib legal coordinate replay",
        "coreChessHeadCommitAtBuild": (
            OPENING_REPLAY_CORECHESS_HEAD_COMMIT
        ),
        "coreChessWorkingTreeAtBuild": "dirty",
        "authoritativeRulesIdentityBasis": (
            "exact Game.cs, ChessLib.csproj, and ChessLib.dll identities; "
            "HEAD commit is a provenance anchor only"
        ),
        "runtime": runtime,
        "runtimeAfterReplay": runtime_after_replay,
        "projection": _content_identity(projection_payload),
        "output": _content_identity(output_payload),
        "requestCount": len(requests),
        "requestSchemaCounts": {
            key: schema_counts[key] for key in sorted(schema_counts)
        },
        "initialSourceCounts": {
            key: initial_source_counts[key]
            for key in sorted(initial_source_counts)
        },
        "sourceArtifactCount": len(source_identities),
        "sourceObjectIdentitiesUnique": True,
        "prefixRecordCount": len(replay_ofens),
        "openingRequestCount": (
            schema_counts["schedule-moves"]
            + schema_counts["event-opening-moves"]
        ),
        "openingPrefixRecordCount": opening_prefix_records,
        "openingPostMovePrefixRecordCount": (
            opening_prefix_records
            - schema_counts["schedule-moves"]
            - schema_counts["event-opening-moves"]
        ),
        "openingExplicitInitialOccurrenceCount": (
            schema_counts["schedule-moves"]
            + schema_counts["event-opening-moves"]
            - initial_source_counts["official-default"]
        ),
        "openingDefaultInitialOccurrenceCount": (
            initial_source_counts["official-default"]
        ),
        "newlyImplicitOpeningOccurrenceCount": (
            opening_prefix_records
            - schema_counts["schedule-moves"]
            - schema_counts["event-opening-moves"]
            + initial_source_counts["official-default"]
        ),
        "transcriptRequestCount": schema_counts["transcript"],
        "transcriptMoveCount": transcript_moves,
        "transcriptPrefixRecordCount": transcript_prefix_records,
        "uniqueOpeningSequenceCount": len(opening_sequences),
        "uniqueOpeningSequenceMoveLengthCounts": {
            str(key): unique_length_counts[key]
            for key in sorted(unique_length_counts)
        },
        "completeSequenceDeduplicatedRootAndPrefixOccurrenceCount": (
            unique_opening_prefix_records
        ),
        "completeSequenceDeduplicatedPostMovePrefixOccurrenceCount": (
            unique_opening_prefix_records - len(opening_sequences)
        ),
        "uniqueOpeningPrefixIdentityCount": len(
            opening_prefix_identities
        ),
        "uniqueOpeningPostMovePrefixIdentityCount": len(
            opening_postmove_prefix_identities
        ),
        "generatedNormalizedOfenCardinality": len(set(replay_ofens)),
        "generatedOpeningNormalizedOfenCardinality": len(
            opening_replay_normalized
        ),
        "generatedTranscriptNormalizedOfenCardinality": len(
            transcript_replay_normalized
        ),
        "plyZeroAndEveryPostMovePrefixEmitted": True,
        "transcriptFullSixFieldParityPassed": True,
        "sourceIdentityReverifiedBeforeAndAfterReplay": True,
        "fullRuntimeBundleReverifiedAfterReplay": True,
        "noClobberOutputAndManifestContractPassed": True,
        "parseOrReplayErrors": 0,
        "targetFieldsDecoded": 0,
        "targetFieldsEmitted": 0,
    }
    _emit_progress(
        progress,
        "opening-replay-complete",
        prefixRecordCount=len(replay_ofens),
        requestCount=len(requests),
    )
    return replay_ofens, audit


def _exclusion_files(roots: Iterable[Path]) -> list[Path]:
    files: set[Path] = set()
    for supplied in roots:
        root = _resolve(supplied)
        destination = _generation3_destination_for(root)
        if destination is not None:
            raise ValueError(
                "generation-3 destination or descendant cannot be a "
                "prior-artifact root: "
                f"root={root}, destination={destination}"
            )
        if not root.exists():
            continue
        if root.is_file():
            if (
                root.suffix.lower() in {".json", ".jsonl"}
                and root not in FRESH_SOURCE_ARTIFACTS
            ):
                files.add(root)
            continue
        for suffix in ("*.json", "*.jsonl"):
            files.update(
                path
                for path in root.rglob(suffix)
                if _generation3_destination_for(path) is None
                and _resolve(path) not in FRESH_SOURCE_ARTIFACTS
            )
    return sorted(files, key=lambda item: str(item).lower())


def _require_stable_exclusion_paths(
    initial: Sequence[Path], final: Sequence[Path]
) -> int:
    initial_set = frozenset(_resolve(path) for path in initial)
    final_set = frozenset(_resolve(path) for path in final)
    if (
        len(initial_set) != len(initial)
        or len(final_set) != len(final)
        or initial_set != final_set
    ):
        added = sorted(
            str(path) for path in final_set - initial_set
        )
        removed = sorted(
            str(path) for path in initial_set - final_set
        )
        raise ValueError(
            "exclusion artifact path set changed while scanned: "
            f"added={added!r}, removed={removed!r}"
        )
    return len(initial_set)


def _final_identity_pass(
    paths: Sequence[Path],
    expected: Sequence[Mapping[str, Any]],
    *,
    label: str,
    progress: ProgressCallback | None = None,
) -> list[dict[str, Any]]:
    if len(paths) != len(expected):
        raise ValueError(f"{label} identity inventory length changed")
    resolved = [_resolve(path) for path in paths]
    if len(frozenset(resolved)) != len(resolved):
        raise ValueError(f"{label} identity inventory has duplicate paths")
    _emit_progress(
        progress,
        f"{label}-final-rehash-start",
        artifactCount=len(resolved),
    )
    actual: list[dict[str, Any]] = []
    for index, path in enumerate(resolved, 1):
        actual.append(_identity(path))
        if index % 100 == 0 or index == len(resolved):
            _emit_progress(
                progress,
                f"{label}-final-rehash-progress",
                artifactOrdinal=index,
                artifactCount=len(resolved),
            )
    if actual != [dict(identity) for identity in expected]:
        raise ValueError(
            f"{label} ordered identity inventory changed after scan"
        )
    _emit_progress(
        progress,
        f"{label}-final-rehash-complete",
        artifactCount=len(resolved),
    )
    return actual


def _strict_canonical_json_bytes(path: Path) -> bytes:
    value = json.loads(
        _resolve(path).read_text(encoding="utf-8-sig"),
        object_pairs_hook=_strict_object_pairs,
    )
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _workspace_complement_sentinel(
    authoritative_files: Sequence[Path],
    *,
    progress: ProgressCallback | None = None,
    identity_sink: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    authoritative = frozenset(
        _resolve(path) for path in authoritative_files
    )
    initial = [
        path
        for path in _exclusion_files([WORKSPACE])
        if _resolve(path) not in authoritative
    ]
    complement_bytes = sum(path.stat().st_size for path in initial)
    _emit_progress(
        progress,
        "workspace-complement-enumerated",
        artifactCount=len(initial),
        sourceBytes=complement_bytes,
    )
    canonical_opening = _identity(CANONICAL_PUBLIC_OPENINGS)
    canonical_fixture = _identity(CANONICAL_OMEGA_RULES_FIXTURE)
    _expect(
        {
            "bytes": canonical_opening["bytes"],
            "sha256": canonical_opening["sha256"],
        },
        {
            "bytes": 2996,
            "sha256": (
                "840faa76475e8297eb73eb33805fabf98202a5002143bb19570"
                "f9ec5aec95955"
            ),
        },
        "canonical public opening identity",
    )
    _expect(
        {
            "bytes": canonical_fixture["bytes"],
            "sha256": canonical_fixture["sha256"],
        },
        {
            "bytes": 647,
            "sha256": (
                "51dfc67c6abd917342bd07cc1e7ce2928a4660315b62cf3b81"
                "94e12854aba939"
            ),
        },
        "canonical Omega fixture identity",
    )
    canonical_opening_semantic = _strict_canonical_json_bytes(
        CANONICAL_PUBLIC_OPENINGS
    )
    identities: list[dict[str, Any]] = []
    key_hits: Counter[str] = Counter()
    raw_ofen_shape_hits = 0
    classified_hits: Counter[str] = Counter()
    classified_hit_identities: list[dict[str, Any]] = []
    unclassified: list[dict[str, Any]] = []
    processed_bytes = 0
    for artifact_ordinal, path in enumerate(initial, 1):
        before = _identity(path)
        text = path.read_text(encoding="utf-8-sig", errors="strict")
        local_key_hits: Counter[str] = Counter()
        local_value_types: Counter[str] = Counter()
        local_raw_ofen_shape_hits = 0
        for match in RAW_JSON_STRING_TOKEN.finditer(text):
            start, end = match.span()
            if _raw_string_has_omega_ofen_shape(text, start, end):
                raw_ofen_shape_hits += 1
                local_raw_ofen_shape_hits += 1
            cursor = end
            while cursor < len(text) and text[cursor] in " \t\r\n":
                cursor += 1
            if cursor >= len(text) or text[cursor] != ":":
                continue
            raw = text[start:end]
            key = (
                raw[1:-1]
                if "\\" not in raw
                else _decode_json_string(
                    text, start, end, location=str(path)
                )
            ).casefold()
            if key in COMPLEMENT_SENTINEL_KEYS:
                key_hits[key] += 1
                local_key_hits[key] += 1
                value_cursor = cursor + 1
                while (
                    value_cursor < len(text)
                    and text[value_cursor] in " \t\r\n"
                ):
                    value_cursor += 1
                value_type = (
                    "string"
                    if value_cursor < len(text)
                    and text[value_cursor] == '"'
                    else "array"
                    if value_cursor < len(text)
                    and text[value_cursor] == "["
                    else "object"
                    if value_cursor < len(text)
                    and text[value_cursor] == "{"
                    else "other"
                )
                local_value_types[f"{key}:{value_type}"] += 1
        after = _identity(path)
        if before != after:
            raise ValueError(
                f"workspace-complement artifact changed while scanned: "
                f"{path}"
            )
        identities.append(after)
        processed_bytes += int(after["bytes"])
        if artifact_ordinal % 100 == 0 or artifact_ordinal == len(initial):
            _emit_progress(
                progress,
                "workspace-complement-progress",
                artifactCount=len(initial),
                artifactOrdinal=artifact_ordinal,
                sourceBytesProcessed=processed_bytes,
                sourceBytesTotal=complement_bytes,
            )
        if local_key_hits or local_raw_ofen_shape_hits:
            allowed = COMPLEMENT_ALLOWED_HIT_CLASSES.get(
                str(after["sha256"])
            )
            if (
                allowed is None
                or int(after["bytes"]) != int(allowed["bytes"])
                or dict(sorted(local_key_hits.items()))
                != dict(sorted(dict(allowed["keys"]).items()))
                or dict(sorted(local_value_types.items()))
                != dict(
                    sorted(dict(allowed["valueTypes"]).items())
                )
                or local_raw_ofen_shape_hits
                != int(allowed["rawOfenShapeHits"])
                or (
                    "normalizedLfSha256" in allowed
                    and hashlib.sha256(
                        path.read_bytes().replace(b"\r\n", b"\n")
                    ).hexdigest()
                    != str(allowed["normalizedLfSha256"])
                )
            ):
                unclassified.append(
                    {
                        "identity": after,
                        "keys": dict(sorted(local_key_hits.items())),
                        "valueTypes": dict(
                            sorted(local_value_types.items())
                        ),
                        "rawOfenShapeHits": (
                            local_raw_ofen_shape_hits
                        ),
                    }
                )
            else:
                class_name = str(allowed["class"])
                if (
                    class_name
                    == "generated-canonical-public-opening-copy"
                    and (
                        after["bytes"] != canonical_opening["bytes"]
                        or after["sha256"] != canonical_opening["sha256"]
                    )
                ):
                    unclassified.append(
                        {
                            "identity": after,
                            "reason": (
                                "canonical opening copy differs from "
                                "authoritative content"
                            ),
                        }
                    )
                    continue
                if (
                    class_name
                    == "generated-omega-rules-fixture-copy"
                    and (
                        after["bytes"] != canonical_fixture["bytes"]
                        or after["sha256"] != canonical_fixture["sha256"]
                    )
                ):
                    unclassified.append(
                        {
                            "identity": after,
                            "reason": (
                                "fixture copy differs from authoritative "
                                "content"
                            ),
                        }
                    )
                    continue
                if (
                    class_name
                    == "generated-style-public-opening-copy"
                    and _strict_canonical_json_bytes(path)
                    != canonical_opening_semantic
                ):
                    unclassified.append(
                        {
                            "identity": after,
                            "reason": (
                                "style opening copy is not strict-JSON "
                                "equivalent to authoritative content"
                            ),
                        }
                    )
                    continue
                classified_hits[class_name] += 1
                classified_hit_identities.append(after)
    final = [
        path
        for path in _exclusion_files([WORKSPACE])
        if _resolve(path) not in authoritative
    ]
    stable_count = _require_stable_exclusion_paths(initial, final)
    identities = _final_identity_pass(
        initial,
        identities,
        label="workspace-complement",
        progress=progress,
    )
    if identity_sink is not None:
        if identity_sink:
            raise ValueError(
                "workspace complement identity sink must begin empty"
            )
        identity_sink.extend(copy.deepcopy(identities))
    identity_payload = _canonical_bytes(identities)
    identity_set = _content_identity(identity_payload)
    classified_identity_set = _content_identity(
        _canonical_bytes(classified_hit_identities)
    )
    if (
        unclassified
        or dict(sorted(classified_hits.items()))
        != EXPECTED_COMPLEMENT_CLASS_COUNTS
        or len(initial) != EXPECTED_COMPLEMENT_ARTIFACT_COUNT
        or sum(int(identity["bytes"]) for identity in identities)
        != EXPECTED_COMPLEMENT_ARTIFACT_BYTES
        or identity_set != EXPECTED_COMPLEMENT_IDENTITY_SET
        or classified_identity_set
        != EXPECTED_COMPLEMENT_CLASSIFIED_IDENTITY_SET
        or dict(sorted(key_hits.items()))
        != EXPECTED_COMPLEMENT_KEY_HITS
        or raw_ofen_shape_hits
        != EXPECTED_COMPLEMENT_RAW_BOARD_SHAPE_HITS
    ):
        raise ValueError(
            "workspace complement classification changed; amend the "
            "authoritative roots or immutable classification before labels: "
            + json.dumps(
                {
                    "artifactCount": len(initial),
                    "artifactBytes": sum(
                        int(identity["bytes"])
                        for identity in identities
                    ),
                    "classified": dict(
                        sorted(classified_hits.items())
                    ),
                    "unclassified": unclassified,
                    "keys": dict(sorted(key_hits.items())),
                    "rawOfenShapeHits": raw_ofen_shape_hits,
                },
                sort_keys=True,
            )
        )
    result = {
        "schemaVersion": 1,
        "kind": "omega-json-workspace-complement-sentinel-v1",
        "workspace": str(WORKSPACE),
        "artifactCount": len(identities),
        "stableArtifactCount": stable_count,
        "artifactBytes": sum(
            int(identity["bytes"]) for identity in identities
        ),
        "orderedIdentitySet": identity_set,
        "classifiedHitArtifactCount": len(
            classified_hit_identities
        ),
        "classifiedHitClassCounts": dict(
            sorted(classified_hits.items())
        ),
        "classifiedHitOrderedIdentitySet": classified_identity_set,
        "triggerKeys": sorted(COMPLEMENT_SENTINEL_KEYS),
        "triggerKeyHits": dict(sorted(key_hits.items())),
        "rawBoardStringShapeHits": raw_ofen_shape_hits,
        "unclassifiedHitArtifactCount": 0,
        "classificationReasons": {
            "generated-canonical-public-opening-copy": (
                "byte-identical to authoritative canonical opening file"
            ),
            "generated-style-public-opening-copy": (
                "strict-JSON canonical bytes equal authoritative canonical "
                "opening file"
            ),
            "generated-omega-rules-fixture-copy": (
                "byte-identical to authoritative Omega rules fixture"
            ),
            "translations": (
                "exact pinned file identity; sole Moves hit is a scalar "
                "string and no raw board-string shape is present"
            ),
        },
        "authoritativeCoverage": {
            "canonicalPublicOpenings": canonical_opening,
            "omegaRulesFixture": canonical_fixture,
        },
        "rawLexicalScanOnly": True,
        "targetFieldsDecoded": 0,
        "targetFieldsEmitted": 0,
        "passed": True,
    }
    _emit_progress(
        progress,
        "workspace-complement-complete",
        artifactCount=len(identities),
        sourceBytes=complement_bytes,
    )
    return result


def _rehash_cached_workspace_complement(
    authoritative_files: Sequence[Path],
    expected_identities: Sequence[Mapping[str, Any]],
    *,
    progress: ProgressCallback | None = None,
) -> list[dict[str, Any]]:
    authoritative = frozenset(
        _resolve(path) for path in authoritative_files
    )
    cached_paths = [
        _resolve(Path(str(identity["path"])))
        for identity in expected_identities
    ]
    current_paths = [
        path
        for path in _exclusion_files([WORKSPACE])
        if _resolve(path) not in authoritative
    ]
    _require_stable_exclusion_paths(cached_paths, current_paths)
    return _final_identity_pass(
        cached_paths,
        expected_identities,
        label="cached-workspace-complement",
        progress=progress,
    )


def _unknown_position_like_keys(
    counts: Mapping[str, int],
) -> dict[str, int]:
    classified = (
        OFEN_FIELDS
        | POSITION_CONTAINER_FIELDS
        | OPAQUE_POSITION_METADATA_FIELDS
    )
    return {
        key: int(counts[key])
        for key in sorted(counts)
        if key not in classified
    }


def _lexical_inventory(
    roots: Iterable[Path],
    *,
    progress: ProgressCallback | None = None,
) -> tuple[
    set[str],
    list[dict[str, Any]],
    int,
    Counter[str],
    int,
    dict[str, Any],
]:
    root_list = [_resolve(path) for path in roots]
    initial_files = _exclusion_files(root_list)
    total_source_bytes = sum(path.stat().st_size for path in initial_files)
    _emit_progress(
        progress,
        "lexical-inventory-enumerated",
        artifactCount=len(initial_files),
        sourceBytes=total_source_bytes,
    )
    signatures: set[str] = set()
    normalized_positions: set[str] = set()
    pins: list[dict[str, Any]] = []
    position_count = 0
    position_like_key_counts: Counter[str] = Counter()
    null_scalar_value_counts: Counter[str] = Counter()
    partial_ofen_canonicalization_counts: Counter[str] = Counter()
    scalar_ofen_arity_counts: Counter[tuple[str, int]] = Counter()
    partial_ofen_canonicalization_events: list[dict[str, Any]] = []
    structural_alias_counts: Counter[str] = Counter()
    structural_alias_field_counts: Counter[str] = Counter()
    replay_requests: list[dict[str, Any]] = []
    processed_source_bytes = 0
    next_byte_milestone = 256 * 1024 * 1024
    for artifact_ordinal, path in enumerate(initial_files, 1):
        before = _identity(path)
        source_alias_lookup = _structural_scalar_alias_lookup(before)
        request_start = len(replay_requests)
        if path.suffix.lower() == ".jsonl":
            with path.open(
                "r", encoding="utf-8-sig", newline=""
            ) as stream:
                for line_number, text in enumerate(stream, 1):
                    if not text.strip():
                        continue
                    location = f"{path}:{line_number}"
                    ofens, key_counts = _lexical_scan_validated(
                        text,
                        location=location,
                        replay_requests=replay_requests,
                        source_record=int(location.rsplit(":", 1)[1]),
                        null_scalar_value_counts=(
                            null_scalar_value_counts
                        ),
                        partial_ofen_canonicalization_counts=(
                            partial_ofen_canonicalization_counts
                        ),
                        scalar_ofen_arity_counts=scalar_ofen_arity_counts,
                        partial_ofen_canonicalization_events=(
                            partial_ofen_canonicalization_events
                        ),
                        source_identity=before,
                        structural_alias_counts=structural_alias_counts,
                        structural_alias_field_counts=(
                            structural_alias_field_counts
                        ),
                        structural_alias_lookup=source_alias_lookup,
                    )
                    position_like_key_counts.update(key_counts)
                    for ofen in ofens:
                        try:
                            signatures.update(
                                deep._leakage_keys(ofen)[2]
                            )
                            normalized_positions.add(
                                _normalize_full_ofen(
                                    ofen,
                                    label=f"{location} OFEN candidate",
                                )
                            )
                        except (TypeError, ValueError):
                            raise ValueError(
                                f"{location}: decoded OFEN failed "
                                "strict exclusion-key validation"
                            )
                        position_count += 1
                    current_source_bytes = min(
                        total_source_bytes,
                        processed_source_bytes
                        + int(stream.buffer.tell()),
                    )
                    if current_source_bytes >= next_byte_milestone:
                        _emit_progress(
                            progress,
                            "lexical-inventory-progress",
                            artifactCount=len(initial_files),
                            artifactOrdinal=artifact_ordinal,
                            sourceBytesProcessed=(
                                current_source_bytes
                            ),
                            sourceBytesTotal=total_source_bytes,
                        )
                        while (
                            current_source_bytes
                            >= next_byte_milestone
                        ):
                            next_byte_milestone += 256 * 1024 * 1024
        else:
            text = path.read_text(encoding="utf-8-sig")
            ofens, key_counts = _lexical_scan_validated(
                text,
                location=str(path),
                replay_requests=replay_requests,
                source_record=1,
                null_scalar_value_counts=null_scalar_value_counts,
                partial_ofen_canonicalization_counts=(
                    partial_ofen_canonicalization_counts
                ),
                scalar_ofen_arity_counts=scalar_ofen_arity_counts,
                partial_ofen_canonicalization_events=(
                    partial_ofen_canonicalization_events
                ),
                source_identity=before,
                structural_alias_counts=structural_alias_counts,
                structural_alias_field_counts=(
                    structural_alias_field_counts
                ),
                structural_alias_lookup=source_alias_lookup,
            )
            position_like_key_counts.update(key_counts)
            for ofen in ofens:
                try:
                    signatures.update(deep._leakage_keys(ofen)[2])
                    normalized_positions.add(
                        _normalize_full_ofen(
                            ofen,
                            label=f"{path} OFEN candidate",
                        )
                    )
                except (TypeError, ValueError):
                    raise ValueError(
                        f"{path}: decoded OFEN failed strict "
                        "exclusion-key validation"
                    )
                position_count += 1
        after = _identity(path)
        if before != after:
            raise ValueError(
                f"exclusion artifact changed while scanned: {path}"
            )
        for request in replay_requests[request_start:]:
            request.pop("source", None)
            request.update(
                {
                    "sourcePath": after["path"],
                    "sourceBytes": after["bytes"],
                    "sourceSha256": after["sha256"],
                }
            )
        pins.append(after)
        processed_source_bytes += int(after["bytes"])
        if (
            artifact_ordinal % 25 == 0
            or processed_source_bytes >= next_byte_milestone
            or artifact_ordinal == len(initial_files)
        ):
            _emit_progress(
                progress,
                "lexical-inventory-progress",
                artifactCount=len(initial_files),
                artifactOrdinal=artifact_ordinal,
                sourceBytesProcessed=processed_source_bytes,
                sourceBytesTotal=total_source_bytes,
            )
            while processed_source_bytes >= next_byte_milestone:
                next_byte_milestone += 256 * 1024 * 1024
    stable_artifact_count = _require_stable_exclusion_paths(
        initial_files, _exclusion_files(root_list)
    )
    expected_structural_alias_counts = (
        _expected_structural_alias_counts(initial_files)
    )
    if structural_alias_counts != expected_structural_alias_counts:
        raise ValueError(
            "pinned structural scalar alias coverage changed: "
            f"expected={dict(sorted(expected_structural_alias_counts.items()))!r}, "
            f"actual={dict(sorted(structural_alias_counts.items()))!r}"
        )
    initial_file_set = set(initial_files)
    expected_structural_alias_field_counts: Counter[str] = Counter(
        str(alias["field"])
        for source in STRUCTURAL_SCALAR_ALIAS_SOURCES
        if _resolve(Path(str(source["path"]))) in initial_file_set
        for alias in source["aliases"]
    )
    if (
        structural_alias_field_counts
        != expected_structural_alias_field_counts
    ):
        raise ValueError(
            "pinned structural scalar alias field coverage changed: "
            f"expected={dict(sorted(expected_structural_alias_field_counts.items()))!r}, "
            f"actual={dict(sorted(structural_alias_field_counts.items()))!r}"
        )
    unknown = _unknown_position_like_keys(position_like_key_counts)
    if unknown:
        raise ValueError(
            "unclassified position-like JSON keys were observed; amend "
            "the target-opaque inventory declaration before labels: "
            + json.dumps(unknown, sort_keys=True)
        )
    partial_arity_entries = [
        {
            "field": field,
            "inputFieldCount": arity,
            "occurrences": scalar_ofen_arity_counts[(field, arity)],
        }
        for field, arity in sorted(scalar_ofen_arity_counts)
        if field in PARTIAL_OFEN_FIELDS
    ]
    partial_event_source_counts: Counter[
        tuple[str, int, str]
    ] = Counter()
    partial_projection_events: list[dict[str, Any]] = []
    for event in partial_ofen_canonicalization_events:
        source = event["source"]
        try:
            relative_event_source = (
                _resolve(Path(str(source["path"])))
                .relative_to(WORKSPACE)
                .as_posix()
            )
        except ValueError as error:
            raise ValueError(
                "canonicalization event source is outside the workspace"
            ) from error
        partial_event_source_counts[
            (
                str(source["path"]),
                int(source["bytes"]),
                str(source["sha256"]),
            )
        ] += 1
        projection_event = copy.deepcopy(event)
        projection_event["source"] = {
            "workspaceRelativePath": relative_event_source,
            "bytes": int(source["bytes"]),
            "sha256": str(source["sha256"]),
        }
        partial_projection_events.append(projection_event)
    partial_source_entries: list[dict[str, Any]] = []
    for (
        source_path,
        source_bytes,
        source_sha256,
    ), occurrences in sorted(partial_event_source_counts.items()):
        try:
            relative_source = (
                _resolve(Path(source_path))
                .relative_to(WORKSPACE)
                .as_posix()
            )
        except ValueError as error:
            raise ValueError(
                "canonicalization event source is outside the workspace"
            ) from error
        partial_source_entries.append(
            {
                "workspaceRelativePath": relative_source,
                "bytes": source_bytes,
                "sha256": source_sha256,
                "occurrences": occurrences,
            }
        )
    partial_count = sum(
        partial_ofen_canonicalization_counts.values()
    )
    four_field_arity_count = sum(
        count
        for (field, arity), count in scalar_ofen_arity_counts.items()
        if field in PARTIAL_OFEN_FIELDS and arity == 4
    )
    if (
        partial_count != four_field_arity_count
        or partial_count != len(partial_ofen_canonicalization_events)
        or partial_count
        != sum(
            int(entry["occurrences"])
            for entry in partial_source_entries
        )
    ):
        raise ValueError(
            "four-field board-key canonicalization telemetry diverged"
        )
    structural_partial_alias_count = sum(
        structural_alias_field_counts[field]
        for field in PARTIAL_OFEN_FIELDS
    )
    accepted_partial_scalar_count = sum(
        count
        for (field, _arity), count in scalar_ofen_arity_counts.items()
        if field in PARTIAL_OFEN_FIELDS
    )
    null_partial_scalar_count = sum(
        null_scalar_value_counts[field]
        for field in PARTIAL_OFEN_FIELDS
    )
    partial_trigger_count = sum(
        position_like_key_counts[field]
        for field in PARTIAL_OFEN_FIELDS
    )
    if partial_trigger_count != (
        accepted_partial_scalar_count
        + null_partial_scalar_count
        + structural_partial_alias_count
    ):
        raise ValueError(
            "four-field board-key trigger reconciliation failed"
        )
    partial_reconciliation = {
        "field": next(iter(sorted(PARTIAL_OFEN_FIELDS))),
        "triggerOccurrences": partial_trigger_count,
        "acceptedScalarOccurrences": accepted_partial_scalar_count,
        "nullOccurrences": null_partial_scalar_count,
        "structuralAliasOccurrences": structural_partial_alias_count,
        "passed": True,
    }
    partial_projection = _content_identity(
        _canonical_bytes(partial_projection_events)
    )
    projected_requests: list[dict[str, Any]] = []
    for request_id, raw in enumerate(replay_requests):
        projected_requests.append(
            {
                "schemaVersion": 1,
                "kind": OPENING_REPLAY_REQUEST_KIND,
                "requestId": request_id,
                "sourcePath": raw["sourcePath"],
                "sourceBytes": raw["sourceBytes"],
                "sourceSha256": raw["sourceSha256"],
                "sourceRecord": raw["sourceRecord"],
                "sourceObjectOrdinal": raw["sourceObjectOrdinal"],
                "sourceObjectIdentity": raw["sourceObjectIdentity"],
                "schema": raw["schema"],
                "initialSource": raw["initialSource"],
                "containerProof": raw["containerProof"],
                "initialOfen": raw["initialOfen"],
                "moves": raw["moves"],
                "expectedPositions": raw["expectedPositions"],
            }
        )
    replay_ofens, replay_audit = _replay_opening_prefixes(
        projected_requests, progress=progress
    )
    raw_signature_count = len(signatures)
    raw_normalized_count = len(normalized_positions)
    replay_signatures: set[str] = set()
    replay_normalized: set[str] = set()
    for index, ofen in enumerate(replay_ofens):
        try:
            keys = deep._leakage_keys(ofen)[2]
            signatures.update(keys)
            replay_signatures.update(keys)
            normalized = _normalize_full_ofen(
                ofen, label=f"opening replay row {index}"
            )
            normalized_positions.add(normalized)
            replay_normalized.add(normalized)
        except (TypeError, ValueError) as error:
            raise ValueError(
                f"opening replay emitted an invalid OFEN at row {index}"
            ) from error
    direct_position_count = position_count
    # Transcript roots and every post-move position were already decoded
    # through InitialOfen + Positions. Explicit opening roots were also
    # decoded directly. Only official-default roots and generated post-move
    # schedule/event prefixes add occurrence rows to the exclusion count.
    position_count += int(
        replay_audit["newlyImplicitOpeningOccurrenceCount"]
    )
    replay_audit["rawDecodedPositionOccurrenceCount"] = (
        direct_position_count
    )
    replay_audit["aggregateExclusionPositionOccurrenceCount"] = (
        position_count
    )
    replay_audit["rawDecodedNormalizedOfenCardinality"] = (
        raw_normalized_count
    )
    replay_audit["rawDecodedConservativeSignatureCardinality"] = (
        raw_signature_count
    )
    replay_audit["generatedReplayNormalizedOfenCardinality"] = len(
        replay_normalized
    )
    replay_audit["generatedReplayConservativeSignatureCardinality"] = (
        len(replay_signatures)
    )
    replay_audit["aggregateNormalizedOfenCardinality"] = len(
        normalized_positions
    )
    replay_audit["aggregateConservativeSignatureCardinality"] = len(
        signatures
    )
    replay_audit["nullScalarValueCount"] = sum(
        null_scalar_value_counts.values()
    )
    replay_audit["nullScalarValueCounts"] = {
        key: null_scalar_value_counts[key]
        for key in sorted(null_scalar_value_counts)
    }
    replay_audit["partialOfenCanonicalizationCount"] = sum(
        partial_ofen_canonicalization_counts.values()
    )
    replay_audit["partialOfenCanonicalizationCounts"] = {
        key: partial_ofen_canonicalization_counts[key]
        for key in sorted(partial_ofen_canonicalization_counts)
    }
    replay_audit["legacyFourFieldBoardKeyArityCountEntries"] = (
        partial_arity_entries
    )
    replay_audit["legacyFourFieldBoardKeySourceEntries"] = (
        partial_source_entries
    )
    replay_audit["legacyFourFieldBoardKeyProjection"] = (
        partial_projection
    )
    replay_audit["legacyFourFieldBoardKeyReconciliation"] = (
        partial_reconciliation
    )
    replay_audit["structuralScalarAliasCount"] = sum(
        structural_alias_counts.values()
    )
    replay_audit["structuralScalarAliasCounts"] = {
        key: structural_alias_counts[key]
        for key in sorted(structural_alias_counts)
    }
    _final_identity_pass(
        initial_files,
        pins,
        label="authoritative-inventory",
        progress=progress,
    )
    _emit_progress(
        progress,
        "lexical-inventory-complete",
        artifactCount=len(pins),
        sourceBytes=total_source_bytes,
    )
    return (
        signatures,
        pins,
        position_count,
        position_like_key_counts,
        stable_artifact_count,
        replay_audit,
    )


def _lexical_read_forbidden(
    roots: Iterable[Path],
) -> tuple[set[str], list[dict[str, Any]], int]:
    (
        signatures,
        pins,
        position_count,
        _key_counts,
        _stable_count,
        _replay_audit,
    ) = _lexical_inventory(roots)
    return signatures, pins, position_count


BASE_PRIOR_ROOTS = (
    REPO / "build-msvc",
    REPO / "build-king-state-v2",
    REPO / "validation",
    WORKSPACE / "match-runs" / "output",
    WORKSPACE / "omega-lab",
)
AMENDMENT_003_ADDITIONAL_ROOTS = (
    ("match-run-configs", WORKSPACE / "match-runs" / "configs"),
    ("match-runs", WORKSPACE / "match-runs"),
    ("opening-audit", WORKSPACE / "opening-audit"),
    ("fixtures", WORKSPACE / "fixtures"),
    (
        "canonical-opening-library",
        WORKSPACE
        / "corechess-arena"
        / "Tools"
        / "OmegaMatch"
        / "Openings",
    ),
)


def _amendment_003_roots() -> list[Path]:
    return [
        _resolve(path)
        for _root_id, path in AMENDMENT_003_ADDITIONAL_ROOTS
    ]


def _require_prior_root_directories(
    supplied: Iterable[Path],
) -> list[Path]:
    roots = [_resolve(path) for path in supplied]
    invalid = [
        str(path)
        for path in roots
        if not path.exists() or not path.is_dir()
    ]
    if invalid:
        raise ValueError(
            "every declared prior-artifact root must exist as a "
            f"directory before labels: {invalid!r}"
        )
    if len(set(roots)) != len(roots):
        raise ValueError("declared prior-artifact roots are not unique")
    return roots


def _prior_roots() -> list[Path]:
    return _require_prior_root_directories(
        (
            *BASE_PRIOR_ROOTS,
            *(path for _root_id, path in AMENDMENT_003_ADDITIONAL_ROOTS),
        )
    )


@contextmanager
def _deep_inventory_patch() -> Iterator[None]:
    original = deep._exclusion_files
    deep._exclusion_files = _exclusion_files
    try:
        yield
    finally:
        deep._exclusion_files = original


@contextmanager
def _rules_only_prepare_patch() -> Iterator[None]:
    original_discover = deep._discover_events
    original_forbidden = deep._read_forbidden

    def no_historical(
        explicit: Iterable[Path],
        roots: Iterable[Path],
        patterns: Sequence[str],
        output_dir: Path,
    ) -> list[Path]:
        if list(explicit) or list(roots):
            raise ValueError(
                "generation 3 forbids every historical event source"
            )
        return []

    deep._discover_events = no_historical
    deep._read_forbidden = _lexical_read_forbidden
    try:
        yield
    finally:
        deep._discover_events = original_discover
        deep._read_forbidden = original_forbidden


def _teacher_artifacts() -> tuple[Path, ...]:
    return (
        RESULTS,
        deep.coordination_guard_path(RESULTS),
        SEARCH_CORPUS,
        SEARCH_MANIFEST,
        HCE_CORPUS,
        HCE_MANIFEST,
        RESIDUAL_CORPUS,
        RESIDUAL_MANIFEST,
    )


def _require_no_teacher_artifacts() -> None:
    existing = [str(path) for path in _teacher_artifacts() if path.exists()]
    if existing:
        raise ValueError(
            "pre-label operation found teacher artifacts: "
            + ", ".join(existing)
        )


def _require_fresh_destinations_absent_or_empty() -> None:
    occupied: list[str] = []
    invalid: list[str] = []
    for supplied in GENERATION3_DESTINATION_SUBTREES:
        destination = _resolve(supplied)
        if not destination.exists():
            continue
        if not destination.is_dir():
            invalid.append(str(destination))
            continue
        try:
            next(destination.iterdir())
        except StopIteration:
            continue
        occupied.append(str(destination))
    if invalid or occupied:
        raise ValueError(
            "amendment-003 can freeze only before every fresh "
            "generation-3 destination is published: "
            f"nonDirectories={invalid!r}, occupied={occupied!r}"
        )


def _sample() -> None:
    _require_verified_amendment_003_preartifact()
    _validate_declarations()
    _verify_prebuilt_tooling()
    if DATA_DIR.exists() and any(DATA_DIR.iterdir()):
        raise ValueError(
            "sample must precede generation-directory publication"
        )
    args = argparse.Namespace(
        dotnet=DOTNET,
        project=SAMPLER_PROJECT,
        assembly=ROOT_SAMPLER_ASSEMBLY,
        managed_runner=lambda command, **kwargs: _run_pinned_dotnet(
            command,
            runtime_bundle_pin=ROOT_SAMPLER_PINS[
                "appLocalRuntimeBundle"
            ],
            **kwargs,
        ),
        output=RULES_ROOTS,
        seed=SEED,
        trajectory_pairs=TRAJECTORY_PAIRS,
        max_plies=220,
        positions_per_phase_side=2,
        capture_percent=72,
        force=False,
    )
    deep._sample(args)


def _prepare() -> None:
    _require_verified_amendment_003_preartifact()
    _validate_declarations()
    _require_no_teacher_artifacts()
    _verify_identity(
        TEACHER_ENGINE_PIN, "architecture-3 teacher executable"
    )
    _verify_prebuilt_tooling()
    for path in (RULES_ROOTS, RULES_MANIFEST, RULES_FREEZE):
        if not path.is_file():
            raise FileNotFoundError(
                f"canonical rules-only sampler artifact missing: {path}"
            )
    args = argparse.Namespace(
        engine=ENGINE,
        harness=HARNESS,
        dotnet=DOTNET,
        managed_runner=lambda command, **kwargs: _run_pinned_dotnet(
            command,
            runtime_bundle_pin=OMEGA_MATCH_PINS[
                "appLocalRuntimeBundle"
            ],
            **kwargs,
        ),
        managed_runner_required=True,
        source_root=[],
        source=[],
        rules_only_root=[RULES_ROOTS],
        exclude_source_pattern=[],
        forbidden_root=_prior_roots(),
        output_dir=DATA_DIR,
        seed=SEED,
        root_pairs=TARGET_PAIRS,
        reserve_pairs_per_phase=RESERVE_PAIRS_PER_PHASE,
        preflight_extra_pairs_per_phase=PREFLIGHT_EXTRA_PAIRS_PER_PHASE,
        max_preflight_rejected_pairs=MAX_PREFLIGHT_REJECTED_PAIRS,
        candidate_pairs_per_trajectory_phase=(
            CANDIDATE_PAIRS_PER_TRAJECTORY_PHASE
        ),
        max_pairs_per_trajectory=MAX_PAIRS_PER_TRAJECTORY,
        max_pairs_per_split_group=MAX_PAIRS_PER_SPLIT_GROUP,
        nodes=TEACHER_NODES,
        acceptance_timeout_seconds=ACCEPTANCE_TIMEOUT_SECONDS,
    )
    with _rules_only_prepare_patch():
        deep._prepare(args)
    _verify_deep_freeze_policy()


def _verify_deep_freeze_policy() -> dict[str, Any]:
    with _deep_inventory_patch():
        context = deep.verify_freeze(DEEP_LOCK)
    lock = context["lock"]
    suite = context["suite"]
    selection = lock.get("selection", {})
    contract = lock.get("selectionContract", {})
    search = lock.get("searchContract", {})
    expected = {
        "targetRootPairs": TARGET_PAIRS,
        "targetPairsPerPhase": TARGET_PAIRS_PER_PHASE,
        "reservePairsPerPhase": RESERVE_PAIRS_PER_PHASE,
        "candidateRootPairs": (
            TARGET_PAIRS + RESERVE_PAIRS_PER_PHASE * len(PHASES)
        ),
        "fixedNodes": TEACHER_NODES,
        "seed": SEED,
    }
    for key, value in expected.items():
        _expect(suite.get(key), value, f"deep suite {key}")
    for key, value in {
        "maximumSelectedPairsPerHistoricalTrajectory": (
            MAX_PAIRS_PER_TRAJECTORY
        ),
        "maximumSelectedPairsPerLaunchFamily": (
            MAX_PAIRS_PER_SPLIT_GROUP
        ),
        "candidatePairsPerTrajectoryPhase": (
            CANDIDATE_PAIRS_PER_TRAJECTORY_PHASE
        ),
        "preflightExtraPairsPerPhase": (
            PREFLIGHT_EXTRA_PAIRS_PER_PHASE
        ),
        "preflightRejectedPairSanityCap": (
            MAX_PREFLIGHT_REJECTED_PAIRS
        ),
    }.items():
        _expect(contract.get(key), value, f"deep selector {key}")
    _expect(search.get("nodes"), TEACHER_NODES, "teacher nodes")
    _expect(selection.get("candidateHistoricalPairs"), 0, "historical pairs")
    _expect(
        selection.get("candidateRulesOnlyPairs"),
        TARGET_PAIRS + RESERVE_PAIRS_PER_PHASE * len(PHASES),
        "rules-only candidate pairs",
    )
    freeze = lock.get("freeze", {})
    if not _same_identity(
        freeze.get("sourceEngine"), TEACHER_ENGINE_PIN
    ):
        raise ValueError(
            "freeze pins a different architecture-3 teacher executable"
        )
    _expect(freeze.get("sourceEvents"), [], "historical source inventory")
    rules = freeze.get("rulesOnlyRootSources")
    if not isinstance(rules, list) or len(rules) != 1:
        raise ValueError("freeze must pin exactly one rules-only root source")
    root_pin = rules[0].get("roots")
    if not _same_identity(root_pin, _identity(RULES_ROOTS)):
        raise ValueError("freeze pins a different rules-only root source")
    if float(
        contract.get("preflightExtraPairsPerPhase", -1)
    ) != PREFLIGHT_EXTRA_PAIRS_PER_PHASE:
        raise ValueError("preflight extra count changed")
    return context


def _projection_rows(
    suite: Mapping[str, Any], *, primary_only: bool
) -> list[dict[str, str]]:
    positions = suite.get("positions")
    if not isinstance(positions, list):
        raise ValueError("teacher suite has no positions")
    components = deep._component_groups(
        [
            {
                "splitGroup": str(item["splitGroup"]),
                "ofen": str(item["initialOfen"]),
            }
            for item in positions
            if isinstance(item, dict)
        ]
    )
    rows: list[dict[str, str]] = []
    for item in positions:
        if not isinstance(item, dict):
            raise ValueError("teacher suite position is not an object")
        if primary_only and item.get("candidateRole") != "primary":
            continue
        phase = str(item.get("phase", ""))
        if phase not in PHASES:
            raise ValueError("teacher suite has an invalid phase")
        split_group = str(item.get("splitGroup", ""))
        rows.append(
            {
                "groupId": components[split_group],
                "ofen": " ".join(str(item["initialOfen"]).split()),
                "phase": phase,
            }
        )
    return rows


def _write_projection(path: Path, rows: Sequence[Mapping[str, str]]) -> None:
    payload = b"".join(
        (
            json.dumps(
                dict(row),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        for row in rows
    )
    try:
        _exclusive_bytes(path, payload)
        return
    except FileExistsError:
        pass
    target = _resolve(path)
    before = _identity(target)
    observed = target.read_bytes()
    after = _identity(target)
    if before != after:
        raise ValueError(
            f"projection changed while verified: {target}"
        )
    if observed != payload:
        raise ValueError(
            f"existing projection differs from deterministic payload: "
            f"{target}"
        )


def _write_or_verify_phase_audit(
    corpus: Path, audit: Path
) -> dict[str, Any]:
    if audit.exists():
        report = incidence.verify_audit(
            corpus,
            audit,
            split_seed=SPLIT_SEED,
            train_percent=TRAIN_PERCENT,
            validation_percent=VALIDATION_PERCENT,
            minimum_groups=2,
        )
    else:
        report = incidence.write_audit(
            corpus,
            audit,
            split_seed=SPLIT_SEED,
            train_percent=TRAIN_PERCENT,
            validation_percent=VALIDATION_PERCENT,
            minimum_groups=2,
        )
    if report.get("passed") is not True:
        raise ValueError(
            f"phase-incidence gate failed and generation is aborted: {audit}"
        )
    for split in report.get("splits", []):
        if (
            not isinstance(split, dict)
            or split.get("passed") is not True
            or int(split.get("minimumObservedGroupCount", 0)) < 2
        ):
            raise ValueError("per-split phase-incidence minimum failed")
    return report


def _load_target_opaque_features(path: Path) -> Any:
    """Build the trainer's feature container from groupId and OFEN only."""

    import numpy as np
    from omega_nnue import deterministic_split

    records: list[tuple[str, str, str]] = []
    splits: list[int] = []
    resolved = _resolve(path)
    with resolved.open(
        "r", encoding="utf-8-sig", newline=""
    ) as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            location = f"{resolved}:{line_number}"
            selected = incidence._selected_strings(  # type: ignore[attr-defined]
                line, location=location, fields=("groupId", "ofen")
            )
            group = selected["groupId"]
            ofen = " ".join(selected["ofen"].split())
            phase, _side = incidence._phase_from_ofen(  # type: ignore[attr-defined]
                ofen, location=location
            )
            records.append((ofen, group, phase))
            splits.append(
                deterministic_split(
                    group,
                    SPLIT_SEED,
                    TRAIN_PERCENT,
                    VALIDATION_PERCENT,
                )
            )
    if not records:
        raise ValueError("feature corpus is empty")
    (
        white,
        black,
        side_to_move,
        signatures,
    ) = generation3_trainer._feature_rows(
        records
    )
    return generation3_trainer.FeatureCorpus(
        ofens=tuple(item[0] for item in records),
        groups=tuple(item[1] for item in records),
        phases=tuple(item[2] for item in records),
        splits=np.asarray(splits, dtype=np.int8),
        white_features=white,
        black_features=black,
        side_to_move_white=side_to_move,
        input_signatures=signatures,
    )


def _collision_counts(
    ofens: Sequence[str],
    signatures: Sequence[str],
    splits: Sequence[Any],
) -> dict[str, int]:
    exact_splits: dict[str, int] = {}
    orbit_splits: dict[str, int] = {}
    exact_cross_split = 0
    orbit_cross_split = 0
    orbit_values: list[str] = []
    for ofen, signature, split_value in zip(
        ofens, signatures, splits
    ):
        split = int(split_value)
        previous = exact_splits.setdefault(signature, split)
        exact_cross_split += previous != split
        orbit = deep._leakage_keys(ofen)[1]
        orbit_values.append(orbit)
        previous_orbit = orbit_splits.setdefault(orbit, split)
        orbit_cross_split += previous_orbit != split
    return {
        "globalExactInputDuplicates": (
            len(signatures) - len(set(signatures))
        ),
        "globalConservativeOrbitDuplicates": (
            len(orbit_values) - len(set(orbit_values))
        ),
        "crossSplitExactInputCollisions": exact_cross_split,
        "crossSplitConservativeOrbitCollisions": orbit_cross_split,
    }


def _feature_gate(path: Path) -> dict[str, Any]:
    features = _load_target_opaque_features(path)
    split_rows = Counter(int(value) for value in features.splits)
    failures: list[str] = []
    for split in range(3):
        if split_rows[split] == 0:
            failures.append(f"split {split} is empty")
    phase_rows: dict[int, Counter[str]] = {
        split: Counter() for split in range(3)
    }
    side_rows: dict[int, Counter[str]] = {
        split: Counter() for split in range(3)
    }
    for index, split_value in enumerate(features.splits):
        split = int(split_value)
        phase_rows[split][features.phases[index]] += 1
        side_rows[split][
            "white" if bool(features.side_to_move_white[index]) else "black"
        ] += 1

    for split in (1, 2):
        rows = split_rows[split]
        if rows == 0:
            continue
        expected = rows / len(PHASES)
        deviation = max(
            abs(phase_rows[split][phase] - expected) / expected
            for phase in PHASES
        )
        imbalance = abs(
            side_rows[split]["white"] - side_rows[split]["black"]
        ) / rows
        if deviation > 0.1:
            failures.append(
                f"split {split} phase relative deviation {deviation:.9g}"
            )
        if imbalance > 0.05:
            failures.append(
                f"split {split} side imbalance {imbalance:.9g}"
            )

    base_piece_features = (
        KING_STATE_OCCUPANCY_FEATURES // KING_BUCKET_COUNT
    )
    occurrences: dict[int, Counter[int]] = {
        split: Counter() for split in range(3)
    }
    buckets: dict[int, set[int]] = {
        split: set() for split in range(3)
    }
    for index, split_value in enumerate(features.splits):
        split = int(split_value)
        for encoded in (
            features.white_features[index],
            features.black_features[index],
        ):
            for value in encoded:
                feature = int(value)
                if feature >= KING_STATE_OCCUPANCY_FEATURES:
                    continue
                occurrences[split][feature] += 1
                buckets[split].add(feature // base_piece_features)
    train_seen = set(occurrences[0])
    unseen_fractions: dict[str, float] = {}
    for split in (1, 2):
        total = sum(occurrences[split].values())
        unseen = sum(
            count
            for feature, count in occurrences[split].items()
            if feature not in train_seen
        )
        fraction = unseen / total if total else 1.0
        unseen_fractions[str(split)] = fraction
        if fraction > 0.02:
            failures.append(
                f"split {split} unseen conditioned occurrence {fraction:.9g}"
            )
        missing = sorted(buckets[split] - buckets[0])
        if missing:
            failures.append(
                f"split {split} king buckets absent from train: {missing}"
            )

    collisions = _collision_counts(
        features.ofens, features.input_signatures, features.splits
    )
    for name, count in collisions.items():
        if count:
            failures.append(f"{count} {name}")

    report = {
        "schemaVersion": 1,
        "kind": "omega-nnue-king-state-v3-feature-only-audit",
        "profileId": PROFILE_ID,
        "corpus": _identity(path),
        "informationBoundary": {
            "decodedValueFields": ["groupId", "ofen"],
            "phaseFieldDecoded": False,
            "phaseDerivedFromOfen": True,
            "targetFieldsDecoded": 0,
            "targetFieldsEmitted": 0,
        },
        "splitSeed": SPLIT_SEED,
        "rows": features.count,
        "splitRows": {
            str(split): split_rows[split] for split in range(3)
        },
        "phaseRows": {
            str(split): dict(phase_rows[split]) for split in range(3)
        },
        "sideToMoveRows": {
            str(split): dict(side_rows[split]) for split in range(3)
        },
        "kingBuckets": {
            str(split): sorted(buckets[split]) for split in range(3)
        },
        "conditionedFeatureOccurrenceFractionUnseenInTrain": (
            unseen_fractions
        ),
        **collisions,
        "failures": failures,
        "passed": not failures,
    }
    return report


def _uniqueness_gate(path: Path) -> dict[str, Any]:
    features = _load_target_opaque_features(path)
    collisions = _collision_counts(
        features.ofens, features.input_signatures, features.splits
    )
    failures = [
        f"{count} {name}"
        for name, count in collisions.items()
        if count
    ]
    report = {
        "schemaVersion": 1,
        "kind": "omega-nnue-king-state-v3-global-uniqueness-audit",
        "profileId": PROFILE_ID,
        "corpus": _identity(path),
        "informationBoundary": {
            "decodedValueFields": ["groupId", "ofen"],
            "phaseFieldDecoded": False,
            "phaseDerivedFromOfen": True,
            "targetFieldsDecoded": 0,
            "targetFieldsEmitted": 0,
        },
        "rows": features.count,
        **collisions,
        "failures": failures,
        "passed": not failures,
    }
    return report


def _write_or_verify_json(
    path: Path, expected: Mapping[str, Any], label: str
) -> None:
    if path.exists():
        actual = _load_json(path, label)
        if actual != dict(expected):
            raise ValueError(f"{label} differs from recomputation")
    else:
        _exclusive_json(path, expected)


def _require_gate_passed(report: Mapping[str, Any], label: str) -> None:
    if report.get("passed") is not True:
        failures = report.get("failures")
        detail = (
            "; ".join(str(value) for value in failures)
            if isinstance(failures, list)
            else "unspecified failure"
        )
        raise ValueError(
            f"{label} failed; generation is aborted and the failure "
            f"artifact is preserved: {detail}"
        )


def _compute_target_free_inventory(
    *,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    (
        signatures,
        pins,
        positions,
        position_like_key_counts,
        stable_artifact_count,
        replay_audit,
    ) = _lexical_inventory(_prior_roots(), progress=progress)
    _expect(
        len(pins),
        EXPECTED_PRIOR_ARTIFACT_COUNT,
        "expanded prior-artifact count",
    )
    complement_identities: list[dict[str, Any]] = []
    complement = _workspace_complement_sentinel(
        [Path(str(pin["path"])) for pin in pins],
        progress=progress,
        identity_sink=complement_identities,
    )
    pins = _final_identity_pass(
        [Path(str(pin["path"])) for pin in pins],
        pins,
        label="authoritative-after-complement",
        progress=progress,
    )
    final_runtime = _opening_replay_identity_bundle()
    _expect(
        final_runtime,
        replay_audit["runtimeAfterReplay"],
        "final opening replay runtime bundle rehash",
    )
    final_target_free_inventory = (
        _final_target_free_inventory_summary(
            signatures=signatures,
            pins=pins,
            positions=positions,
            position_like_key_counts=position_like_key_counts,
            stable_artifact_count=stable_artifact_count,
            replay_audit=replay_audit,
            complement=complement,
        )
    )
    return {
        "signatures": signatures,
        "pins": pins,
        "positions": positions,
        "positionLikeKeyCounts": position_like_key_counts,
        "stableArtifactCount": stable_artifact_count,
        "replayAudit": replay_audit,
        "complement": complement,
        "complementIdentities": complement_identities,
        "summary": final_target_free_inventory,
    }


def _document_target_free_contribution(
    path: Path,
    document: Mapping[str, Any],
) -> dict[str, Any]:
    identity = _document_identity(path, document)
    null_counts: Counter[str] = Counter()
    partial_counts: Counter[str] = Counter()
    structural_counts: Counter[str] = Counter()
    replay_requests: list[dict[str, Any]] = []
    ofens, key_counts = _lexical_scan_validated(
        _canonical_bytes(document).decode("utf-8"),
        location=f"virtual:{_resolve(path)}",
        replay_requests=replay_requests,
        source_record=1,
        null_scalar_value_counts=null_counts,
        partial_ofen_canonicalization_counts=partial_counts,
        source_identity=identity,
        structural_alias_counts=structural_counts,
        structural_alias_lookup=_structural_scalar_alias_lookup(identity),
    )
    return {
        "normalizedBoardStateOccurrences": dict(
            sorted(
                Counter(
                    _normalize_full_ofen(
                        ofen,
                        label=f"virtual {_resolve(path)} OFEN",
                    )
                    for ofen in ofens
                ).items()
            )
        ),
        "positionLikeTriggerCounts": dict(sorted(key_counts.items())),
        "nullScalarValueCounts": dict(sorted(null_counts.items())),
        "partialOfenCanonicalizationCounts": dict(
            sorted(partial_counts.items())
        ),
        "structuralScalarAliasCounts": dict(
            sorted(structural_counts.items())
        ),
        "replayRequestCount": len(replay_requests),
        "replayRequests": replay_requests,
        "targetFieldsDecoded": 0,
        "targetFieldsEmitted": 0,
    }


def _replace_virtual_identity(
    pins: Sequence[Mapping[str, Any]],
    replacement: Mapping[str, Any],
) -> list[dict[str, Any]]:
    replacement_path = _resolve(Path(str(replacement["path"])))
    result: list[dict[str, Any]] = []
    replaced = 0
    for raw in pins:
        current = dict(raw)
        if _resolve(Path(str(current["path"]))) == replacement_path:
            current = dict(replacement)
            replaced += 1
        result.append(current)
    if replaced != 1:
        raise ValueError(
            "virtual declaration replacement did not match exactly one "
            f"authoritative artifact: {replacement_path}"
        )
    return result


def _build_frozen_amendment_003(
    draft: Mapping[str, Any],
    *,
    created_utc: str,
    inventory: Mapping[str, Any],
    summary: Mapping[str, Any],
) -> dict[str, Any]:
    value = copy.deepcopy(dict(draft))
    value["status"] = FROZEN_AMENDMENT_003_STATUS
    value["createdUtc"] = created_utc
    evidence = value.get("classifiedExistingEvidence")
    if not isinstance(evidence, dict):
        raise ValueError("draft amendment-003 evidence is absent")
    replay_evidence = evidence.get("openingAndTranscriptReplay")
    if not isinstance(replay_evidence, dict):
        raise ValueError("draft amendment-003 replay evidence is absent")
    replay_audit = inventory["replayAudit"]
    for key in tuple(replay_evidence):
        if key == "fullSixFieldTranscriptParityMismatches":
            replay_evidence[key] = 0
        elif key == "legacyFourFieldBoardKeyCount":
            replay_evidence[key] = replay_audit[
                "partialOfenCanonicalizationCount"
            ]
        elif key in {
            "nullScalarValueCountEntries",
            "legacyFourFieldBoardKeyCountEntries",
        }:
            audit_key = {
                "nullScalarValueCountEntries": "nullScalarValueCounts",
                "legacyFourFieldBoardKeyCountEntries": (
                    "partialOfenCanonicalizationCounts"
                ),
            }[key]
            replay_evidence[key] = _count_evidence(
                replay_audit[audit_key]
            )
        elif key in replay_audit:
            replay_evidence[key] = copy.deepcopy(replay_audit[key])
        else:
            raise ValueError(
                f"draft replay evidence key has no computed value: {key}"
            )
    evidence["finalTargetFreeInventory"] = copy.deepcopy(dict(summary))
    authoritative = summary.get("authoritativeUnion")
    if not isinstance(authoritative, Mapping):
        raise ValueError("candidate summary authoritative union is absent")
    evidence["authoritativeUnion"] = {
        "artifacts": int(authoritative["artifactCountDecimal"]),
        "bytes": int(authoritative["artifactBytesDecimal"]),
        "stablePathSetRequired": True,
    }
    return value


def _build_match_document_candidates(
    protocol: Mapping[str, Any],
    adapter: Mapping[str, Any],
    amendment_identity: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    expected_chain = [
        _relative_identity(AMENDMENT_PIN),
        _relative_identity(AMENDMENT_002_PIN),
        _relative_identity(amendment_identity),
    ]
    protocol_candidate = copy.deepcopy(dict(protocol))
    protocol_candidate["amendmentChain"] = copy.deepcopy(expected_chain)
    adapter_candidate = copy.deepcopy(dict(adapter))
    declarations = adapter_candidate.get("declarations")
    if not isinstance(declarations, dict):
        raise ValueError("match adapter declarations are absent")
    declarations["amendmentChain"] = copy.deepcopy(expected_chain)
    _validate_match_declaration_transaction(
        amendment_identity,
        protocol=protocol_candidate,
        adapter=adapter_candidate,
    )
    return protocol_candidate, adapter_candidate


def _candidate_inventory_summary(
    inventory: Mapping[str, Any],
    pins: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    return _final_target_free_inventory_summary(
        signatures=inventory["signatures"],
        pins=pins,
        positions=inventory["positions"],
        position_like_key_counts=inventory["positionLikeKeyCounts"],
        stable_artifact_count=inventory["stableArtifactCount"],
        replay_audit=inventory["replayAudit"],
        complement=inventory["complement"],
    )


def _fixed_point_until_two_complete_states(
    initial_state: Any,
    step: Callable[[Any], tuple[Any, bool, str]],
    *,
    maximum_passes: int = 8,
) -> tuple[Any, int, str]:
    state = initial_state
    previous_complete_fingerprint: str | None = None
    complete_repeats = 0
    for pass_number in range(1, maximum_passes + 1):
        state, complete, fingerprint = step(state)
        if complete:
            if fingerprint == previous_complete_fingerprint:
                complete_repeats += 1
            else:
                complete_repeats = 1
            previous_complete_fingerprint = fingerprint
            if complete_repeats >= 2:
                return state, pass_number, fingerprint
        else:
            previous_complete_fingerprint = None
            complete_repeats = 0
    raise ValueError(
        "amendment-003 freeze candidate did not reach two consecutive "
        f"complete fixed states within {maximum_passes} passes"
    )


def _stabilize_amendment_003_candidates(
    *,
    draft: Mapping[str, Any],
    protocol: Mapping[str, Any],
    adapter: Mapping[str, Any],
    created_utc: str,
    inventory: Mapping[str, Any],
    maximum_passes: int = 8,
) -> tuple[dict[str, Any], int, str]:
    baseline_contributions = {
        str(path): _document_target_free_contribution(path, document)
        for path, document in (
            (AMENDMENT_003, draft),
            (MATCH_PROTOCOL, protocol),
            (MATCH_ADAPTER, adapter),
        )
    }

    def step(state: Mapping[str, Any]) -> tuple[dict[str, Any], bool, str]:
        embedded_summary = state["summary"]
        amendment = _build_frozen_amendment_003(
            draft,
            created_utc=created_utc,
            inventory=inventory,
            summary=embedded_summary,
        )
        amendment_identity = _document_identity(
            AMENDMENT_003, amendment
        )
        _require_five_digit_amendment_size(amendment_identity)
        protocol_candidate, adapter_candidate = (
            _build_match_document_candidates(
                protocol,
                adapter,
                amendment_identity,
            )
        )
        documents = (
            (AMENDMENT_003, amendment),
            (MATCH_PROTOCOL, protocol_candidate),
            (MATCH_ADAPTER, adapter_candidate),
        )
        for path, document in documents:
            candidate_contribution = _document_target_free_contribution(
                path, document
            )
            if (
                candidate_contribution
                != baseline_contributions[str(path)]
            ):
                raise ValueError(
                    "virtual freeze replacement is not target-free "
                    f"inventory neutral: {path}"
                )
        protocol_identity = _document_identity(
            MATCH_PROTOCOL, protocol_candidate
        )
        adapter_identity = _document_identity(
            MATCH_ADAPTER, adapter_candidate
        )
        virtual_pins = [dict(pin) for pin in inventory["pins"]]
        for replacement in (
            amendment_identity,
            protocol_identity,
            adapter_identity,
        ):
            virtual_pins = _replace_virtual_identity(
                virtual_pins, replacement
            )
        derived_summary = _candidate_inventory_summary(
            inventory, virtual_pins
        )
        derived_bytes = sum(int(pin["bytes"]) for pin in virtual_pins)
        complete = (
            embedded_summary == derived_summary
            and amendment["classifiedExistingEvidence"][
                "authoritativeUnion"
            ]["bytes"]
            == derived_bytes
        )
        ordered_identity_set = _content_identity(
            _canonical_bytes(virtual_pins)
        )
        fingerprint_payload = {
            "amendment": amendment_identity,
            "protocol": protocol_identity,
            "adapter": adapter_identity,
            "orderedAuthoritativeIdentitySet": ordered_identity_set,
            "summary": derived_summary,
            "targetFieldsDecoded": 0,
            "targetFieldsEmitted": 0,
        }
        fingerprint = _content_identity(
            _canonical_bytes(fingerprint_payload)
        )["sha256"]
        return (
            {
                "summary": derived_summary,
                "amendment": amendment,
                "protocol": protocol_candidate,
                "adapter": adapter_candidate,
                "amendmentIdentity": amendment_identity,
                "protocolIdentity": protocol_identity,
                "adapterIdentity": adapter_identity,
                "virtualPins": virtual_pins,
                "orderedAuthoritativeIdentitySet": (
                    ordered_identity_set
                ),
                "baselineContributions": baseline_contributions,
                "fingerprintPayload": fingerprint_payload,
            },
            complete,
            fingerprint,
        )

    initial = {"summary": inventory["summary"]}
    final, passes, fingerprint = _fixed_point_until_two_complete_states(
        initial,
        step,
        maximum_passes=maximum_passes,
    )
    return final, passes, fingerprint


def _freeze_required_substitutions(
    state: Mapping[str, Any],
    inventory: Mapping[str, Any],
) -> list[dict[str, Any]]:
    amendment_identity = state["amendmentIdentity"]
    return [
        {
            "path": AMENDMENT_003.relative_to(REPO).as_posix(),
            "operation": "replace-entire-file-with-canonical-document",
            "identity": amendment_identity,
        },
        {
            "path": MATCH_PROTOCOL.relative_to(REPO).as_posix(),
            "operation": "replace-entire-file-with-canonical-document",
            "identity": state["protocolIdentity"],
        },
        {
            "path": MATCH_ADAPTER.relative_to(REPO).as_posix(),
            "operation": "replace-entire-file-with-canonical-document",
            "identity": state["adapterIdentity"],
        },
        {
            "path": "tools/omega_nnue/king_state_v3.py",
            "symbols": {
                "AMENDMENT_003_PIN": amendment_identity,
                "EXPECTED_PRIOR_ARTIFACT_BYTES": sum(
                    int(pin["bytes"]) for pin in state["virtualPins"]
                ),
                "EXPECTED_PRIOR_ORDERED_IDENTITY_SET": (
                    state["orderedAuthoritativeIdentitySet"]
                ),
                "EXPECTED_REPLAY_AUDIT": {
                    key: copy.deepcopy(inventory["replayAudit"][key])
                    for key in EXPECTED_REPLAY_AUDIT
                },
                "EXPECTED_FINAL_TARGET_FREE_INVENTORY": state["summary"],
            },
        },
        {
            "path": (
                "tools/omega_nnue/"
                "king_state_train_generation3.py"
            ),
            "symbols": {
                "AMENDMENT_003_BYTES": amendment_identity["bytes"],
                "AMENDMENT_003_SHA256": amendment_identity["sha256"],
            },
        },
        {
            "path": (
                "tools/omega_nnue/"
                "king_state_match_readiness_generation3.py"
            ),
            "symbol": "V3_AMENDMENT_CHAIN_PINS[2]",
            "value": {
                "bytes": amendment_identity["bytes"],
                "sha256": amendment_identity["sha256"],
            },
        },
    ]


def _emit_proposal_report(
    report: Mapping[str, Any],
    *,
    output: Path | None,
) -> None:
    payload = _canonical_bytes(report)
    if output is None:
        sys.stdout.buffer.write(payload)
        sys.stdout.buffer.flush()
        return
    target = _resolve(output)
    try:
        target.relative_to(WORKSPACE)
    except ValueError:
        pass
    else:
        raise ValueError(
            "proposal output must be external to the shared workspace"
        )
    _exclusive_bytes(target, payload)
    print(str(target))


def _propose_amendment_003_freeze(
    *,
    created_utc: str,
    output: Path | None,
) -> None:
    created_utc = _validate_strict_utc(
        created_utc, label="--created-utc"
    )
    _require_fresh_destinations_absent_or_empty()
    _validate_declarations(mode="draft")
    draft = _load_json_strict(
        AMENDMENT_003, "draft amendment-003"
    )
    protocol = _load_json_strict(MATCH_PROTOCOL, "match protocol")
    adapter = _load_json_strict(MATCH_ADAPTER, "match adapter")
    _validate_match_declaration_transaction(
        AMENDMENT_003_PIN,
        protocol=protocol,
        adapter=adapter,
    )
    draft_identities = {
        "amendment": _identity(AMENDMENT_003),
        "protocol": _identity(MATCH_PROTOCOL),
        "adapter": _identity(MATCH_ADAPTER),
    }
    inventory = _compute_target_free_inventory(
        progress=_stderr_progress
    )
    state, passes, fingerprint = (
        _stabilize_amendment_003_candidates(
            draft=draft,
            protocol=protocol,
            adapter=adapter,
            created_utc=created_utc,
            inventory=inventory,
        )
    )
    # Rehash the complete actual source boundary after the in-memory fixed
    # point with an authoritative/complement/authoritative sequence.  No
    # payload is reparsed and no candidate document is written.
    authoritative_paths = [
        Path(str(pin["path"])) for pin in inventory["pins"]
    ]
    _final_identity_pass(
        authoritative_paths,
        inventory["pins"],
        label="proposal-post-fixed-point-authoritative-a",
        progress=_stderr_progress,
    )
    _rehash_cached_workspace_complement(
        authoritative_paths,
        inventory["complementIdentities"],
        progress=_stderr_progress,
    )
    _final_identity_pass(
        authoritative_paths,
        inventory["pins"],
        label="proposal-post-fixed-point-authoritative-b",
        progress=_stderr_progress,
    )
    _expect(
        _opening_replay_identity_bundle(),
        inventory["replayAudit"]["runtimeAfterReplay"],
        "proposal post-fixed-point replay runtime",
    )
    _expect(
        {
            "amendment": _identity(AMENDMENT_003),
            "protocol": _identity(MATCH_PROTOCOL),
            "adapter": _identity(MATCH_ADAPTER),
        },
        draft_identities,
        "proposal source declaration identities",
    )
    _require_fresh_destinations_absent_or_empty()
    report = {
        "schemaVersion": 1,
        "kind": "omega-nnue-king-state-v3-amendment-003-freeze-proposal",
        "profileId": PROFILE_ID,
        "createdUtc": created_utc,
        "readOnly": True,
        "authoritativeHistoricalPayloadScanCount": 1,
        "workspaceComplementPayloadScanCount": 1,
        "postFixedPointIdentityOnlyRehashSequence": [
            "authoritative",
            "workspace-complement",
            "authoritative",
        ],
        "fixedPointPasses": passes,
        "completeFixedPointStatesRequired": 2,
        "fixedPointFingerprint": fingerprint,
        "draftIdentities": draft_identities,
        "candidateDocuments": {
            "amendment": {
                "identity": state["amendmentIdentity"],
                "document": state["amendment"],
            },
            "protocol": {
                "identity": state["protocolIdentity"],
                "document": state["protocol"],
            },
            "adapter": {
                "identity": state["adapterIdentity"],
                "document": state["adapter"],
            },
        },
        "orderedAuthoritativeIdentitySet": (
            state["orderedAuthoritativeIdentitySet"]
        ),
        "finalTargetFreeInventory": state["summary"],
        "targetNeutralReplacementProofs": (
            state["baselineContributions"]
        ),
        "requiredSubstitutions": _freeze_required_substitutions(
            state, inventory
        ),
        "informationBoundary": {
            "targetFieldsDecoded": 0,
            "targetFieldsEmitted": 0,
            "historicalEvidenceOnly": True,
        },
    }
    _emit_proposal_report(report, output=output)


def _require_canonical_document(
    path: Path,
    document: Mapping[str, Any],
    *,
    label: str,
) -> None:
    if _resolve(path).read_bytes() != _canonical_bytes(document):
        raise ValueError(
            f"{label} is not encoded as the exact canonical JSON payload"
        )


def _verify_amendment_003_pass() -> dict[str, Any]:
    _validate_declarations(mode="frozen")
    _require_no_teacher_artifacts()
    amendment = _load_json_strict(
        AMENDMENT_003, "frozen amendment-003"
    )
    protocol = _load_json_strict(MATCH_PROTOCOL, "frozen match protocol")
    adapter = _load_json_strict(MATCH_ADAPTER, "frozen match adapter")
    for path, document, label in (
        (AMENDMENT_003, amendment, "frozen amendment-003"),
        (MATCH_PROTOCOL, protocol, "frozen match protocol"),
        (MATCH_ADAPTER, adapter, "frozen match adapter"),
    ):
        _require_canonical_document(
            path, document, label=label
        )
    amendment_identity = _identity(AMENDMENT_003)
    _require_five_digit_amendment_size(amendment_identity)
    _validate_match_declaration_transaction(
        amendment_identity,
        protocol=protocol,
        adapter=adapter,
    )
    inventory = _compute_target_free_inventory(
        progress=_stderr_progress
    )
    pins = inventory["pins"]
    replay_audit = inventory["replayAudit"]
    summary = inventory["summary"]
    _expect(
        len(pins),
        EXPECTED_PRIOR_ARTIFACT_COUNT,
        "verified authoritative artifact count",
    )
    _expect(
        sum(int(pin["bytes"]) for pin in pins),
        EXPECTED_PRIOR_ARTIFACT_BYTES,
        "verified authoritative artifact bytes",
    )
    ordered_identity_set = _content_identity(_canonical_bytes(pins))
    if EXPECTED_PRIOR_ORDERED_IDENTITY_SET is None:
        raise ValueError(
            "verified authoritative ordered identity-set constant is absent"
        )
    _expect(
        ordered_identity_set,
        EXPECTED_PRIOR_ORDERED_IDENTITY_SET,
        "verified authoritative ordered identity set",
    )
    for key, expected in EXPECTED_REPLAY_AUDIT.items():
        _expect(
            replay_audit.get(key),
            expected,
            f"verified replay evidence {key}",
        )
    if EXPECTED_FINAL_TARGET_FREE_INVENTORY is None:
        raise ValueError(
            "verified final target-free inventory constant is absent"
        )
    _expect(
        summary,
        EXPECTED_FINAL_TARGET_FREE_INVENTORY,
        "verified target-free inventory constant",
    )
    evidence = amendment.get("classifiedExistingEvidence")
    if not isinstance(evidence, dict):
        raise ValueError("frozen amendment evidence is absent")
    _expect(
        evidence.get("finalTargetFreeInventory"),
        summary,
        "verified frozen target-free inventory evidence",
    )
    _expect(
        evidence.get("authoritativeUnion"),
        {
            "artifacts": len(pins),
            "bytes": sum(int(pin["bytes"]) for pin in pins),
            "stablePathSetRequired": True,
        },
        "verified frozen authoritative union",
    )
    replay_evidence = evidence.get("openingAndTranscriptReplay")
    if not isinstance(replay_evidence, dict):
        raise ValueError("frozen replay evidence is absent")
    for key, expected in EXPECTED_REPLAY_AUDIT.items():
        evidence_key = {
            "nullScalarValueCounts": "nullScalarValueCountEntries",
            "partialOfenCanonicalizationCount": (
                "legacyFourFieldBoardKeyCount"
            ),
            "partialOfenCanonicalizationCounts": (
                "legacyFourFieldBoardKeyCountEntries"
            ),
        }.get(key, key)
        expected_value = (
            _count_evidence(expected)
            if key
            in {
                "nullScalarValueCounts",
                "partialOfenCanonicalizationCounts",
            }
            else expected
        )
        _expect(
            replay_evidence.get(evidence_key),
            expected_value,
            f"verified frozen replay declaration {key}",
        )
    for forbidden_map in (
        "nullScalarValueCounts",
        "partialOfenCanonicalizationCounts",
    ):
        if forbidden_map in replay_evidence:
            raise ValueError(
                "frozen replay field-count telemetry is not "
                "target-neutral"
            )
    _expect(
        replay_evidence.get("fullSixFieldTranscriptParityMismatches"),
        0,
        "verified frozen transcript parity declaration",
    )
    protocol_identity = _identity(MATCH_PROTOCOL)
    adapter_identity = _identity(MATCH_ADAPTER)
    fingerprint_payload = {
        "amendment": amendment_identity,
        "protocol": protocol_identity,
        "adapter": adapter_identity,
        "orderedAuthoritativeIdentitySet": ordered_identity_set,
        "summary": summary,
        "targetFieldsDecoded": 0,
        "targetFieldsEmitted": 0,
    }
    fingerprint = _content_identity(
        _canonical_bytes(fingerprint_payload)
    )["sha256"]
    return {
        "schemaVersion": 1,
        "kind": (
            "omega-nnue-king-state-v3-amendment-003-verification-pass"
        ),
        "profileId": PROFILE_ID,
        "passed": True,
        "fingerprint": fingerprint,
        "fingerprintPayload": fingerprint_payload,
        "targetFieldsDecoded": 0,
        "targetFieldsEmitted": 0,
    }


def _fresh_amendment_003_verification_processes() -> dict[str, Any]:
    reports: list[dict[str, Any]] = []
    script = (
        "import sys;"
        f"sys.path.insert(0,{str(Path(__file__).parent)!r});"
        "import king_state_v3 as verifier;"
        "sys.stdout.buffer.write(verifier._canonical_bytes("
        "verifier._verify_amendment_003_pass()))"
    )
    for process_ordinal in range(1, 3):
        _stderr_progress(
            "fresh-verification-process-start",
            {"processOrdinal": process_ordinal, "processCount": 2},
        )
        completed = subprocess.run(
            [sys.executable, "-B", "-c", script],
            cwd=REPO,
            text=True,
            encoding="utf-8",
            errors="strict",
            stdout=subprocess.PIPE,
            check=False,
        )
        if completed.returncode != 0:
            raise ValueError(
                "fresh amendment-003 verification process failed: "
                f"ordinal={process_ordinal}, exit={completed.returncode}"
            )
        try:
            report = json.loads(
                completed.stdout,
                object_pairs_hook=_strict_object_pairs,
            )
        except (json.JSONDecodeError, ValueError) as error:
            raise ValueError(
                "fresh amendment-003 verifier emitted invalid JSON"
            ) from error
        if not isinstance(report, dict) or report.get("passed") is not True:
            raise ValueError(
                "fresh amendment-003 verifier did not emit a pass report"
            )
        reports.append(report)
        _stderr_progress(
            "fresh-verification-process-complete",
            {"processOrdinal": process_ordinal, "processCount": 2},
        )
    _expect(
        reports[1],
        reports[0],
        "two fresh amendment-003 verification passes",
    )
    return {
        "schemaVersion": 1,
        "kind": (
            "omega-nnue-king-state-v3-amendment-003-verification"
        ),
        "profileId": PROFILE_ID,
        "freshProcessPasses": 2,
        "identicalCompleteStatesRequired": 2,
        "fingerprint": reports[0]["fingerprint"],
        "pass": reports[0],
        "passed": True,
        "targetFieldsDecoded": 0,
        "targetFieldsEmitted": 0,
    }


def _verify_amendment_003() -> None:
    report = _fresh_amendment_003_verification_processes()
    sys.stdout.buffer.write(_canonical_bytes(report))
    sys.stdout.buffer.flush()


def _require_verified_amendment_003_preartifact() -> None:
    report = _fresh_amendment_003_verification_processes()
    if report.get("passed") is not True:
        raise ValueError(
            "amendment-003 verification did not pass before publication"
        )


def _catalog_from_freeze(context: Mapping[str, Any]) -> dict[str, Any]:
    inventory = _compute_target_free_inventory(
        progress=_stderr_progress
    )
    signatures = inventory["signatures"]
    pins = inventory["pins"]
    positions = inventory["positions"]
    position_like_key_counts = inventory["positionLikeKeyCounts"]
    stable_artifact_count = inventory["stableArtifactCount"]
    replay_audit = inventory["replayAudit"]
    complement = inventory["complement"]
    final_target_free_inventory = inventory["summary"]
    _expect(
        sum(int(pin["bytes"]) for pin in pins),
        EXPECTED_PRIOR_ARTIFACT_BYTES,
        "expanded prior-artifact bytes",
    )
    if EXPECTED_PRIOR_ORDERED_IDENTITY_SET is None:
        raise ValueError(
            "expanded prior ordered identity-set expectation is absent"
        )
    _expect(
        _content_identity(_canonical_bytes(pins)),
        EXPECTED_PRIOR_ORDERED_IDENTITY_SET,
        "expanded prior ordered identity set",
    )
    for key, expected in EXPECTED_REPLAY_AUDIT.items():
        _expect(
            replay_audit.get(key),
            expected,
            f"expanded replay audit {key}",
        )
    amendment_003 = _load_json(
        AMENDMENT_003, "generation-3 inventory correction"
    )
    declared_final_inventory = (
        amendment_003.get("classifiedExistingEvidence", {}).get(
            "finalTargetFreeInventory"
        )
        if isinstance(
            amendment_003.get("classifiedExistingEvidence"), dict
        )
        else None
    )
    _expect(
        declared_final_inventory,
        final_target_free_inventory,
        "recomputed final target-free inventory",
    )
    lock = context["lock"]
    frozen = lock["freeze"].get("forbiddenArtifacts")
    if not isinstance(frozen, list):
        raise ValueError("deep freeze has no exclusion inventory")
    if pins != frozen:
        raise ValueError(
            "lexical exclusion inventory differs from deep freeze pins"
        )
    _expect(
        stable_artifact_count,
        len(pins),
        "stable lexical exclusion artifact count",
    )
    frozen_signatures = lock["freeze"].get(
        "forbiddenInputSignatures"
    )
    if (
        not isinstance(frozen_signatures, list)
        or signatures != set(str(value) for value in frozen_signatures)
    ):
        raise ValueError(
            "lexical forbidden signatures differ from deep freeze"
        )
    _expect(
        lock["freeze"].get("forbiddenPositionsRead"),
        positions,
        "lexical forbidden position count",
    )
    history = _resolve(
        REPO / "build-king-state-v2" / "readiness"
        / "history.positions.jsonl"
    )
    if not any(
        _resolve(Path(str(pin["path"]))) == history for pin in pins
    ):
        raise ValueError("history snapshot is absent from exclusion catalog")
    for prior in (
        _resolve(DATA_PARENT / "deep-hce-v2"),
        _resolve(DATA_PARENT / "deep-hce-v3"),
    ):
        if not any(prior in Path(str(pin["path"])).parents for pin in pins):
            raise ValueError(
                f"prior generation corpus root was not inventoried: {prior}"
            )
    declared_roots = _prior_roots()
    root_artifact_counts = {
        str(root): sum(
            1
            for pin in pins
            if (
                _resolve(Path(str(pin["path"]))) == root
                or root in _resolve(Path(str(pin["path"]))).parents
            )
        )
        for root in declared_roots
    }
    return {
        "schemaVersion": 1,
        "kind": "omega-nnue-king-state-v3-target-opaque-exclusion-catalog",
        "profileId": PROFILE_ID,
        "scanner": _identity(Path(__file__)),
        "phaseAndSymmetryImplementation": _identity(
            Path(incidence.__file__)
        ),
        "declaredRoots": [str(path) for path in declared_roots],
        "allDeclaredRootsExistedAsDirectories": True,
        "declaredRootArtifactCounts": root_artifact_counts,
        "amendment003RequiredRoots": [
            str(path) for path in _amendment_003_roots()
        ],
        "artifacts": pins,
        "artifactCount": len(pins),
        "stableArtifactCount": stable_artifact_count,
        "artifactBytes": sum(int(pin["bytes"]) for pin in pins),
        "postScanArtifactPathSetMatched": True,
        "rawOfenFamilyAndContainerValuesDecoded": replay_audit[
            "rawDecodedPositionOccurrenceCount"
        ],
        "generatedOpeningReplayRows": replay_audit[
            "openingPrefixRecordCount"
        ],
        "generatedTranscriptParityRows": replay_audit[
            "transcriptPrefixRecordCount"
        ],
        "newlyImplicitOpeningRowsAddedToExclusionCount": replay_audit[
            "newlyImplicitOpeningOccurrenceCount"
        ],
        "forbiddenPositionOccurrenceCount": positions,
        "forbiddenInputSignatureCount": len(signatures),
        "decodedValueFields": sorted(OFEN_FIELDS),
        "selectedStringLeafContainers": sorted(
            POSITION_CONTAINER_FIELDS
        ),
        "opaquePositionLikeMetadataFields": sorted(
            OPAQUE_POSITION_METADATA_FIELDS
        ),
        "positionLikeKeyCounts": {
            key: position_like_key_counts[key]
            for key in sorted(position_like_key_counts)
        },
        "unclassifiedPositionLikeKeys": [],
        "allOtherValuesSkippedLexically": True,
        "ordinaryStringRawOfenShapeGuard": True,
        "openingAndTranscriptReplay": replay_audit,
        "workspaceComplementSentinel": complement,
        "finalTargetFreeInventory": final_target_free_inventory,
        "targetFieldsDecoded": 0,
        "targetFieldsEmitted": 0,
        "includesGeneration1Corpus": True,
        "includesGeneration2Corpus": True,
        "includesHistorySnapshot": True,
        "destinationExcluded": str(DATA_DIR),
        "freshGenerationDestinationSubtreesExcluded": [
            str(_resolve(path))
            for path in GENERATION3_DESTINATION_SUBTREES
        ],
        "postPublicationVerificationUsesSameDestinationExclusions": True,
        "freshRulesOnlySourceExcludedAndPinnedSeparately": [
            str(path) for path in sorted(
                FRESH_SOURCE_ARTIFACTS, key=lambda item: str(item).lower()
            )
        ],
    }


def _audit_freeze() -> None:
    _require_verified_amendment_003_preartifact()
    profile, amendment = _validate_declarations()
    _require_no_teacher_artifacts()
    context = _verify_deep_freeze_policy()
    suite = context["suite"]
    candidate_rows = _projection_rows(suite, primary_only=False)
    final_rows = _projection_rows(suite, primary_only=True)
    _expect(
        len(candidate_rows),
        (TARGET_PAIRS + RESERVE_PAIRS_PER_PHASE * len(PHASES)) * 2,
        "candidate projection rows",
    )
    _expect(len(final_rows), TARGET_PAIRS * 2, "final projection rows")
    _write_projection(CANDIDATE_PROJECTION, candidate_rows)
    _write_projection(FINAL_ROOT_PROJECTION, final_rows)
    candidate = _write_or_verify_phase_audit(
        CANDIDATE_PROJECTION, CANDIDATE_AUDIT
    )
    final = _write_or_verify_phase_audit(
        FINAL_ROOT_PROJECTION, FINAL_ROOT_AUDIT
    )
    candidate_uniqueness = _uniqueness_gate(CANDIDATE_PROJECTION)
    _write_or_verify_json(
        CANDIDATE_UNIQUENESS_AUDIT,
        candidate_uniqueness,
        "candidate-pool uniqueness audit",
    )
    _require_gate_passed(
        candidate_uniqueness, "candidate-pool uniqueness gate"
    )
    feature = _feature_gate(FINAL_ROOT_PROJECTION)
    _write_or_verify_json(FEATURE_AUDIT, feature, "feature audit")
    _require_gate_passed(feature, "feature-only prelabel gate")
    catalog = _catalog_from_freeze(context)
    _write_or_verify_json(
        EXCLUSION_CATALOG, catalog, "exclusion catalog"
    )
    report = {
        "schemaVersion": 1,
        "kind": "omega-nnue-king-state-v3-freeze-audit",
        "profileId": PROFILE_ID,
        "informationBoundary": {
            "teacherLabelsExisted": False,
            "targetFieldsDecoded": 0,
            "targetFieldsEmitted": 0,
        },
        "identities": {
            "preregistration": _identity(PREREGISTRATION),
            "amendment": _identity(AMENDMENT),
            "amendment002": _identity(AMENDMENT_002),
            "amendment003": _identity(AMENDMENT_003),
            "orderedAmendments": [
                _identity(AMENDMENT),
                _identity(AMENDMENT_002),
                _identity(AMENDMENT_003),
            ],
            "generation2Incident": _identity(GEN2_INCIDENT),
            "deepFreeze": _identity(DEEP_LOCK),
            "teacherSuite": _identity(DEEP_SUITE),
            "candidateProjection": _identity(CANDIDATE_PROJECTION),
            "finalRootProjection": _identity(FINAL_ROOT_PROJECTION),
            "candidatePhaseAudit": _identity(CANDIDATE_AUDIT),
            "finalRootPhaseAudit": _identity(FINAL_ROOT_AUDIT),
            "featureAudit": _identity(FEATURE_AUDIT),
            "candidatePoolUniquenessAudit": _identity(
                CANDIDATE_UNIQUENESS_AUDIT
            ),
            "exclusionCatalog": _identity(EXCLUSION_CATALOG),
        },
        "candidatePhasePassed": candidate["passed"],
        "finalRootPhasePassed": final["passed"],
        "featureGatesPassed": feature["passed"],
        "candidatePoolUniquenessPassed": candidate_uniqueness["passed"],
        "fixedSelectorParameters": {
            "preflightExtraPairsPerPhase": (
                PREFLIGHT_EXTRA_PAIRS_PER_PHASE
            ),
            "preflightRejectedPairSanityCap": (
                MAX_PREFLIGHT_REJECTED_PAIRS
            ),
            "maximumSelectedPairsPerTrajectory": (
                MAX_PAIRS_PER_TRAJECTORY
            ),
            "maximumSelectedPairsPerSplitGroup": (
                MAX_PAIRS_PER_SPLIT_GROUP
            ),
            "candidatePairsPerTrajectoryPerPhase": (
                CANDIDATE_PAIRS_PER_TRAJECTORY_PHASE
            ),
            "acceptanceTimeoutSeconds": ACCEPTANCE_TIMEOUT_SECONDS,
        },
        "rulesOnlySources": True,
        "passed": True,
    }
    _write_or_verify_json(FREEZE_AUDIT, report, "freeze audit")
    del profile, amendment


def _matches_expected(path: Path, expected: Mapping[str, Any]) -> bool:
    try:
        return _same_identity(_identity(path), expected)
    except FileNotFoundError:
        return False


def _resolve_initializer() -> None:
    _require_verified_amendment_003_preartifact()
    _validate_declarations()
    _require_no_teacher_artifacts()
    _verify_deep_freeze_policy()
    if not FREEZE_AUDIT.is_file():
        raise FileNotFoundError(
            "run audit-freeze before resolve-initializer"
        )
    priority_one = all(
        (
            _matches_expected(K2_NETWORK, K2_EXPECTED["network"]),
            _matches_expected(K2_MANIFEST, K2_EXPECTED["manifest"]),
            _matches_expected(GEN2_SELECTION, K2_EXPECTED["selection"]),
            _matches_expected(GEN2_READINESS, K2_EXPECTED["readiness"]),
            _matches_expected(GEN2_INCIDENT, INCIDENT_PIN),
        )
    )
    if priority_one:
        selected_id = "K2"
        priority = 1
        network = K2_NETWORK
        manifest = K2_MANIFEST
        evidence = {
            key: _identity(Path(str(value["path"])))
            for key, value in K2_EXPECTED.items()
        }
        reason = (
            "Exact public generation-2 identities establish validation-selected "
            "K2, passed deployment/readiness gates, and retirement only at the "
            "post-selection offline structural stage."
        )
    else:
        if not (
            _matches_expected(K0_NETWORK, K0_EXPECTED["network"])
            and _matches_expected(K0_MANIFEST, K0_EXPECTED["manifest"])
        ):
            raise ValueError(
                "K2 priority-one evidence is incomplete and exact K0 fallback "
                "is unavailable; initializer resolution aborts"
            )
        selected_id = "K0"
        priority = 2
        network = K0_NETWORK
        manifest = K0_MANIFEST
        evidence = {
            "network": _identity(K0_NETWORK),
            "manifest": _identity(K0_MANIFEST),
        }
        reason = (
            "At least one K2 priority-one public identity was absent or "
            "mismatched; the exact preregistered K0 fallback is selected."
        )
    resolution = {
        "schemaVersion": 1,
        "kind": "omega-nnue-king-state-v3-initializer-resolution",
        "profileId": PROFILE_ID,
        "createdUtc": _utc_now(),
        "decisionMoment": "before generation-3 teacher labels",
        "selectedInitializer": selected_id,
        "selectedPriority": priority,
        "network": _identity(network),
        "manifest": _identity(manifest),
        "publicEvidence": evidence,
        "declarations": {
            "preregistration": _identity(PREREGISTRATION),
            "amendment": _identity(AMENDMENT),
            "amendment002": _identity(AMENDMENT_002),
            "amendment003": _identity(AMENDMENT_003),
            "orderedAmendments": [
                _identity(AMENDMENT),
                _identity(AMENDMENT_002),
                _identity(AMENDMENT_003),
            ],
            "generation2Incident": _identity(GEN2_INCIDENT),
            "freezeAudit": _identity(FREEZE_AUDIT),
        },
        "reason": reason,
        "informationBoundary": {
            "generation2TargetFieldsDecoded": 0,
            "generation2NumericMetricsUsed": False,
            "publicStatusAndIdentityMetadataOnly": True,
            "generation3LabelsExisted": False,
        },
        "immutableAfterPublication": True,
    }
    _exclusive_json(INITIALIZER_RESOLUTION, resolution)


def _walk_identity_values(value: Any) -> Iterator[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        if {"path", "bytes", "sha256"}.issubset(value):
            yield value
            return
        for child in value.values():
            yield from _walk_identity_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_identity_values(child)


def _teacher_selector_contract() -> dict[str, Any]:
    return {
        "samplerAndSelectorSeed": SEED,
        "preflightExtraPairsPerPhase": PREFLIGHT_EXTRA_PAIRS_PER_PHASE,
        "preflightRejectedPairSanityCap": (
            MAX_PREFLIGHT_REJECTED_PAIRS
        ),
        "maximumSelectedPairsPerTrajectory": (
            MAX_PAIRS_PER_TRAJECTORY
        ),
        "maximumSelectedPairsPerSplitGroup": (
            MAX_PAIRS_PER_SPLIT_GROUP
        ),
        "candidatePairsPerTrajectoryPerPhase": (
            CANDIDATE_PAIRS_PER_TRAJECTORY_PHASE
        ),
        "acceptanceTimeoutSeconds": ACCEPTANCE_TIMEOUT_SECONDS,
    }


def _generation3_training_contract() -> dict[str, Any]:
    """Obtain the trainer-owned contract in a fresh deterministic process."""

    script = (
        "import json,sys;"
        f"sys.path.insert(0,{str(Path(__file__).parent)!r});"
        "import king_state_train_generation3 as trainer;"
        "print(json.dumps(trainer.prelabel_training_contract(),"
        "sort_keys=True,allow_nan=False))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
        timeout=120,
    )
    if completed.returncode != 0:
        raise ValueError(
            "generation-3 trainer contract subprocess failed: "
            + (completed.stderr.strip() or completed.stdout.strip())
        )
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise ValueError(
            "generation-3 trainer contract output is invalid JSON"
        ) from error
    if (
        not isinstance(value, dict)
        or value.get("kind")
        != "omega-nnue-king-state-v3-prelabel-training-contract"
        or value.get("profileId") != PROFILE_ID
    ):
        raise ValueError("generation-3 trainer returned a wrong contract")
    return value


def _verify_audit_set() -> None:
    _write_or_verify_phase_audit(
        CANDIDATE_PROJECTION, CANDIDATE_AUDIT
    )
    _write_or_verify_phase_audit(
        FINAL_ROOT_PROJECTION, FINAL_ROOT_AUDIT
    )
    feature = _feature_gate(FINAL_ROOT_PROJECTION)
    _write_or_verify_json(FEATURE_AUDIT, feature, "feature audit")
    _require_gate_passed(feature, "feature-only prelabel gate")
    candidate_uniqueness = _uniqueness_gate(CANDIDATE_PROJECTION)
    _write_or_verify_json(
        CANDIDATE_UNIQUENESS_AUDIT,
        candidate_uniqueness,
        "candidate-pool uniqueness audit",
    )
    _require_gate_passed(
        candidate_uniqueness, "candidate-pool uniqueness gate"
    )
    context = _verify_deep_freeze_policy()
    catalog = _catalog_from_freeze(context)
    _write_or_verify_json(
        EXCLUSION_CATALOG, catalog, "exclusion catalog"
    )
    freeze = _load_json(FREEZE_AUDIT, "freeze audit")
    if freeze.get("passed") is not True:
        raise ValueError("freeze audit did not pass")


def _seal() -> None:
    _require_verified_amendment_003_preartifact()
    _validate_declarations()
    _require_no_teacher_artifacts()
    context = _verify_deep_freeze_policy()
    _verify_audit_set()
    resolution = _load_json(
        INITIALIZER_RESOLUTION, "initializer resolution"
    )
    if (
        resolution.get("profileId") != PROFILE_ID
        or resolution.get("selectedInitializer") not in {"K2", "K0"}
    ):
        raise ValueError("initializer resolution is invalid")
    for identity in _walk_identity_values(resolution):
        _verify_identity(identity, "initializer-resolution pin")
    static_hce = _identity(STATIC_HCE)
    _expect(
        static_hce["sha256"], STATIC_HCE_SHA256, "static HCE evaluator"
    )
    lock = context["lock"]
    training_contract = _generation3_training_contract()
    seal = {
        "schemaVersion": 1,
        # Compatibility envelope consumed by deep_hce_v2 run/finalize.
        "kind": "omega-nnue-king-state-v1-prelabel-seal",
        "generationId": "king-state-v1-deep-hce-v2",
        "createdUtc": _utc_now(),
        "profile": {
            "kind": "omega-nnue-king-state-v3-preregistration",
            "profileId": PROFILE_ID,
            "dataProfile": "deep-hce-v4",
            "protocolGeneration": 3,
            "compatibilityEnvelope": (
                "legacy kind/generation retained solely for sealed "
                "deep_hce_v2 run/finalize transport"
            ),
        },
        "declaration": {
            "effectiveBeforeTeacherLabels": True,
            "teacherArtifactsAbsentAtDeclaration": True,
            "rulesOnlySources": True,
            "targetFieldsDecodedByPrelabelAudits": 0,
        },
        "contracts": {
            "labelsPermittedOnlyAfterSeal": True,
            "deepHceTargetPairs": TARGET_PAIRS,
            "deepHceTargetRoots": TARGET_PAIRS * 2,
            "deepHceReservePairsPerPhase": RESERVE_PAIRS_PER_PHASE,
            "teacherNodesPerRoot": TEACHER_NODES,
            "minimumGroupsPerObservedPhaseIncidenceStratumPerSplit": 2,
            "preclaimReverificationRequired": True,
            "inProfileStructuralRepairAllowed": False,
        },
        "teacherSelectorContract": _teacher_selector_contract(),
        "trainingContract": training_contract,
        "identities": {
            "deepHceFreeze": context["lockIdentity"],
            "teacherSuite": context["suiteIdentity"],
            "frozenTeacherEngine": context["engineIdentity"],
            "teacherLegalityConfig": lock["selection"][
                "legalityValidationConfig"
            ],
            "sourceTeacherEngine": lock["freeze"]["sourceEngine"],
            "staticHceEvaluator": static_hce,
            "preregistration": _identity(PREREGISTRATION),
            "amendment": _identity(AMENDMENT),
            "amendment002": _identity(AMENDMENT_002),
            "amendment003": _identity(AMENDMENT_003),
            "orderedAmendments": [
                _identity(AMENDMENT),
                _identity(AMENDMENT_002),
                _identity(AMENDMENT_003),
            ],
            "generation2Incident": _identity(GEN2_INCIDENT),
            "initializerResolution": _identity(
                INITIALIZER_RESOLUTION
            ),
            "resolvedInitializer": dict(resolution["network"]),
            "resolvedInitializerManifest": dict(
                resolution["manifest"]
            ),
            "candidateProjection": _identity(CANDIDATE_PROJECTION),
            "finalRootProjection": _identity(FINAL_ROOT_PROJECTION),
            "candidatePhaseAudit": _identity(CANDIDATE_AUDIT),
            "finalRootPhaseAudit": _identity(FINAL_ROOT_AUDIT),
            "featureAudit": _identity(FEATURE_AUDIT),
            "candidatePoolUniquenessAudit": _identity(
                CANDIDATE_UNIQUENESS_AUDIT
            ),
            "exclusionCatalog": _identity(EXCLUSION_CATALOG),
            "freezeAudit": _identity(FREEZE_AUDIT),
            "tooling": {
                "deepHceV2": _identity(Path(deep.__file__)),
                "generation3Wrapper": _identity(Path(__file__)),
                "phaseIncidencePreflight": _identity(
                    Path(incidence.__file__)
                ),
                "generation3Trainer": _identity(
                    Path(__file__).with_name(
                        "king_state_train_generation3.py"
                    )
                ),
                "baseTrainer": _identity(
                    Path(__file__).with_name("train.py")
                ),
                "omegaNnue": _identity(
                    Path(__file__).with_name("omega_nnue.py")
                ),
                "legacyKingStateOrchestrator": _identity(
                    Path(__file__).with_name("king_state_train.py")
                ),
                "amendedIncidenceOrchestrator": _identity(
                    Path(__file__).with_name(
                        "king_state_train_amended.py"
                    )
                ),
                "symmetryAndSelection": _identity(
                    Path(__file__).with_name("select_screen.py")
                ),
                "staticHceLabeler": _identity(
                    Path(__file__).with_name("label_hce.py")
                ),
                "residualBuilder": _identity(
                    Path(__file__).with_name(
                        "build_residual_targets.py"
                    )
                ),
                "compiledCppParityHelper": static_hce,
                "openingReplay": {
                    key: _identity(Path(str(pin["path"])))
                    for key, pin in OPENING_REPLAY_PINS.items()
                },
            },
        },
    }
    _exclusive_json(PRELABEL_SEAL, seal)
    _verify_seal()


def _verify_seal() -> dict[str, Any]:
    _validate_declarations()
    context = _verify_deep_freeze_policy()
    seal = _load_json(PRELABEL_SEAL, "generation-3 prelabel seal")
    _expect(
        seal.get("kind"),
        "omega-nnue-king-state-v1-prelabel-seal",
        "compatibility seal kind",
    )
    _expect(
        seal.get("generationId"),
        "king-state-v1-deep-hce-v2",
        "compatibility generation id",
    )
    profile = seal.get("profile")
    if not isinstance(profile, dict):
        raise ValueError("seal has no generation-3 profile")
    _expect(profile.get("profileId"), PROFILE_ID, "sealed profile id")
    _expect(
        profile.get("protocolGeneration"), 3, "sealed protocol generation"
    )
    _expect(
        seal.get("trainingContract"),
        _generation3_training_contract(),
        "sealed generation-3 training contract",
    )
    _expect(
        seal.get("teacherSelectorContract"),
        _teacher_selector_contract(),
        "sealed teacher selector contract",
    )
    for identity in _walk_identity_values(seal.get("identities", {})):
        _verify_identity(identity, "prelabel-seal pin")
    _verify_audit_set()
    resolution = _load_json(
        INITIALIZER_RESOLUTION, "initializer resolution"
    )
    identities = seal.get("identities")
    if not isinstance(identities, dict):
        raise ValueError("seal has no identity inventory")
    _expect(
        identities.get("orderedAmendments"),
        [
            _identity(AMENDMENT),
            _identity(AMENDMENT_002),
            _identity(AMENDMENT_003),
        ],
        "sealed ordered amendment chain",
    )
    tooling = identities.get("tooling")
    if not isinstance(tooling, dict):
        raise ValueError("seal has no tooling identity inventory")
    _expect(
        tooling.get("openingReplay"),
        {
            key: _identity(Path(str(pin["path"])))
            for key, pin in OPENING_REPLAY_PINS.items()
        },
        "sealed opening replay runtime bundle",
    )
    if not _same_identity(
        identities.get("resolvedInitializer"), resolution.get("network")
    ):
        raise ValueError(
            "seal direct initializer pin differs from resolution"
        )
    if not _same_identity(
        identities.get("resolvedInitializerManifest"),
        resolution.get("manifest"),
    ):
        raise ValueError(
            "seal direct initializer-manifest pin differs from resolution"
        )
    for identity in _walk_identity_values(resolution):
        _verify_identity(identity, "initializer-resolution pin")
    compatibility = deep._verify_prelabel_seal(
        PRELABEL_SEAL, context
    )
    return {
        "seal": _identity(PRELABEL_SEAL),
        "profileId": PROFILE_ID,
        "initializer": resolution["selectedInitializer"],
        "compatibilityIdentityCount": compatibility["identityCount"],
        "passed": True,
    }


def _run(*, jobs: int, limit: int | None, timeout: float) -> None:
    if jobs < 1:
        raise ValueError("--jobs must be positive")
    if limit is not None and limit < 1:
        raise ValueError("--limit must be positive")
    _verify_seal()
    # Recompute both per-split structural audits at the last point before the
    # first teacher process can start.
    _write_or_verify_phase_audit(
        CANDIDATE_PROJECTION, CANDIDATE_AUDIT
    )
    _write_or_verify_phase_audit(
        FINAL_ROOT_PROJECTION, FINAL_ROOT_AUDIT
    )
    with _deep_inventory_patch():
        deep._run(
            argparse.Namespace(
                lock=DEEP_LOCK,
                seal=PRELABEL_SEAL,
                limit=limit,
                jobs=jobs,
                timeout_seconds=timeout,
            )
        )


def _finalize() -> None:
    _verify_seal()
    with _deep_inventory_patch():
        deep._finalize(
            argparse.Namespace(lock=DEEP_LOCK, seal=PRELABEL_SEAL)
        )
    _audit_finalized()


def _audit_finalized() -> None:
    _verify_seal()
    if not SEARCH_CORPUS.is_file() or not SEARCH_MANIFEST.is_file():
        raise FileNotFoundError("finalized search corpus is absent")
    report = _write_or_verify_phase_audit(
        SEARCH_CORPUS, FINAL_SEARCH_AUDIT
    )
    feature = _feature_gate(SEARCH_CORPUS)
    _write_or_verify_json(
        FINAL_SEARCH_FEATURE_AUDIT,
        feature,
        "finalized search feature audit",
    )
    _require_gate_passed(feature, "finalized search feature gate")
    if report.get("rows") != TARGET_PAIRS * 2:
        raise ValueError("finalized search corpus has the wrong row count")
    _expect(feature["rows"], TARGET_PAIRS * 2, "search feature rows")


def _build_residual() -> None:
    _audit_finalized()
    if any(
        path.exists()
        for path in (
            HCE_CORPUS,
            HCE_MANIFEST,
            RESIDUAL_CORPUS,
            RESIDUAL_MANIFEST,
        )
    ):
        raise ValueError("refusing to overwrite residual-stage artifacts")
    import label_hce
    import build_residual_targets

    seal = _load_json(PRELABEL_SEAL, "prelabel seal")
    static_pin = seal["identities"]["staticHceEvaluator"]
    evaluator = _verify_identity(static_pin, "static HCE evaluator")
    label_hce.label_jsonl(
        input_path=SEARCH_CORPUS,
        output_path=HCE_CORPUS,
        manifest_path=HCE_MANIFEST,
        evaluator_path=evaluator,
    )
    build_residual_targets.build_residual_targets(
        search_path=SEARCH_CORPUS,
        handcrafted_path=HCE_CORPUS,
        output_path=RESIDUAL_CORPUS,
        manifest_path=RESIDUAL_MANIFEST,
    )
    _verify_corpus()


def _verify_corpus() -> None:
    _verify_seal()
    for path in (RESIDUAL_CORPUS, RESIDUAL_MANIFEST):
        if not path.is_file():
            raise FileNotFoundError(path)
    phase = _write_or_verify_phase_audit(
        RESIDUAL_CORPUS, FINAL_RESIDUAL_AUDIT
    )
    # This duplicate, independently pinned audit is the artifact that must be
    # recomputed/verified immediately before any one-time access claim.
    preclaim = _write_or_verify_phase_audit(
        RESIDUAL_CORPUS, PRECLAIM_AUDIT
    )
    feature = _feature_gate(RESIDUAL_CORPUS)
    _write_or_verify_json(
        FINAL_FEATURE_AUDIT, feature, "finalized feature audit"
    )
    _require_gate_passed(feature, "finalized residual feature gate")
    if phase.get("rows") != TARGET_PAIRS * 2:
        raise ValueError("residual corpus has the wrong row count")
    if preclaim != phase:
        raise ValueError("preclaim audit differs from final corpus audit")
    report = {
        "schemaVersion": 1,
        "kind": "omega-nnue-king-state-v3-corpus-verification",
        "profileId": PROFILE_ID,
        "informationBoundary": {
            "decodedValueFields": ["groupId", "ofen"],
            "phaseFieldDecoded": False,
            "phaseDerivedFromOfen": True,
            "targetFieldsDecoded": 0,
            "targetFieldsEmitted": 0,
        },
        "identities": {
            "residualCorpus": _identity(RESIDUAL_CORPUS),
            "residualManifest": _identity(RESIDUAL_MANIFEST),
            "phaseAudit": _identity(FINAL_RESIDUAL_AUDIT),
            "preclaimAudit": _identity(PRECLAIM_AUDIT),
            "featureAudit": _identity(FINAL_FEATURE_AUDIT),
            "prelabelSeal": _identity(PRELABEL_SEAL),
        },
        "rows": phase["rows"],
        "phaseIncidencePassed": phase["passed"],
        "preclaimPassed": preclaim["passed"],
        "featureGatesPassed": feature["passed"],
        "passed": True,
    }
    _write_or_verify_json(
        CORPUS_VERIFICATION, report, "corpus verification"
    )


def _self_test() -> None:
    amendment_003 = _load_json_strict(
        AMENDMENT_003, "self-test amendment-003"
    )
    validation_mode = (
        "draft"
        if amendment_003.get("status") == DRAFT_AMENDMENT_003_STATUS
        else "frozen"
    )
    _validate_declarations(mode=validation_mode)
    if generation3_trainer.NUMPY_WAS_PRELOADED:
        raise AssertionError(
            "generation-3 trainer loaded after NumPy; runtime contract unsound"
        )
    if (
        generation3_trainer.prelabel_training_contract()
        != _generation3_training_contract()
    ):
        raise AssertionError(
            "in-process and fresh-process training contracts differ"
        )
    valid_ofen = incidence._synthetic_ofen(  # type: ignore[attr-defined]
        "endgame", "w", 7
    )
    poison = "TARGET-VALUE-MUST-NOT-BE-DECODED"
    text = json.dumps(
        {
            "targetCpStm": poison,
            "nested": {"initialOfen": valid_ofen},
            "score": {"pv": poison},
        }
    )
    decoded: list[str] = []
    original = globals()["_decode_json_string"]

    def recording(
        value: str, start: int, end: int, *, location: str
    ) -> str:
        result = original(value, start, end, location=location)
        decoded.append(result)
        return result

    globals()["_decode_json_string"] = recording
    try:
        strict_ofens = _lexical_ofens(
            text, location="lexical-self-test-strict"
        )
        validated_ofens = _lexical_ofens_validated(
            text, location="lexical-self-test-tokenized"
        )
    finally:
        globals()["_decode_json_string"] = original
    if (
        strict_ofens != [valid_ofen]
        or validated_ofens != [valid_ofen]
        or poison in decoded
    ):
        raise AssertionError("lexical scanner decoded a target/score value")

    def must_reject(payload: str, label: str) -> None:
        try:
            _lexical_ofens_validated(payload, location=label)
        except ValueError:
            return
        raise AssertionError(f"structural scanner accepted {label}")

    must_reject(
        r'{"target":"unterminated, \"initialOfen\": \"hidden\"}',
        "unclosed-string-before-hidden-ofen",
    )
    must_reject(
        '{"target":"bad"quote","initialOfen":'
        + json.dumps(valid_ofen)
        + "}",
        "unescaped-quote",
    )
    fake = json.dumps(
        {"note": '"initialOfen":' + json.dumps(valid_ofen)}
    )
    if _lexical_ofens_validated(
        fake, location="fake-ofen-inside-string"
    ):
        raise AssertionError("scanner treated ordinary string text as a key")
    nested = json.dumps(
        {
            "number": -1250.0,
            "literal": True,
            "array": [None, {"initialOfen": valid_ofen}],
        }
    )
    if _lexical_ofens_validated(
        nested, location="nested-object-array"
    ) != [valid_ofen]:
        raise AssertionError("scanner missed a nested OFEN")
    escaped_key = (
        '{"initial\\u004ffen":' + json.dumps(valid_ofen) + "}"
    )
    if _lexical_ofens_validated(
        escaped_key, location="escaped-ofen-key"
    ) != [valid_ofen]:
        raise AssertionError("scanner missed an escaped OFEN key")
    null_scalar_counts: Counter[str] = Counter()
    null_scalar_ofens, null_scalar_keys = _lexical_scan_validated(
        '{"postOfen":null}',
        location="null-scalar-ofen",
        null_scalar_value_counts=null_scalar_counts,
    )
    if (
        null_scalar_ofens
        or null_scalar_keys != Counter({"postofen": 1})
        or null_scalar_counts != Counter({"postofen": 1})
    ):
        raise AssertionError(
            "scanner did not count scalar OFEN null as absent"
        )
    for invalid_scalar, label in (
        ("17", "number"),
        ("true", "true"),
        ("false", "false"),
        ("[]", "array"),
        ("{}", "object"),
        (json.dumps("not-an-omega-ofen"), "invalid-string"),
    ):
        must_reject(
            '{"postOfen":' + invalid_scalar + "}",
            f"scalar-ofen-{label}",
        )
    observed_structural_aliases: Counter[str] = Counter()
    observed_structural_keys: Counter[str] = Counter()
    for source in STRUCTURAL_SCALAR_ALIAS_SOURCES:
        source_path = Path(str(source["path"]))
        source_identity = _identity(source_path)
        local_aliases: Counter[str] = Counter()
        _source_ofens, source_keys = _lexical_scan_validated(
            source_path.read_text(encoding="utf-8-sig"),
            location=f"structural-alias-source:{source_path}",
            source_identity=source_identity,
            structural_alias_counts=local_aliases,
        )
        observed_structural_aliases.update(local_aliases)
        observed_structural_keys.update(source_keys)
    if (
        observed_structural_aliases
        != Counter(EXPECTED_STRUCTURAL_SCALAR_ALIAS_COUNTS)
        or {
            key: observed_structural_keys[key]
            for key in ("fen", "position", "ofen", "postofen")
        }
        != {"fen": 5, "position": 5, "ofen": 5, "postofen": 1}
    ):
        raise AssertionError(
            "pinned structural scalar alias coverage drifted"
        )

    suite_source = STRUCTURAL_SCALAR_ALIAS_SOURCES[2]
    suite_identity = {
        key: suite_source[key] for key in ("path", "bytes", "sha256")
    }

    def must_reject_structural(
        payload: str,
        identity: Mapping[str, Any],
        label: str,
    ) -> None:
        try:
            _lexical_scan_validated(
                payload,
                location=label,
                source_identity=identity,
                structural_alias_counts=Counter(),
            )
        except ValueError:
            return
        raise AssertionError(
            f"structural scanner accepted alias mutation {label}"
        )

    exact_suite_shape = {"minLength": 1, "type": "string"}
    must_reject_structural(
        json.dumps(
            {"other": {"ofen": exact_suite_shape}},
            sort_keys=True,
        ),
        suite_identity,
        "structural-alias-wrong-pointer",
    )
    wrong_identity = dict(suite_identity)
    wrong_identity["sha256"] = "0" * 64
    must_reject_structural(
        json.dumps(
            {
                "$defs": {
                    "case": {
                        "properties": {"ofen": exact_suite_shape}
                    }
                }
            },
            sort_keys=True,
        ),
        wrong_identity,
        "structural-alias-wrong-source-identity",
    )
    must_reject_structural(
        json.dumps(
            {
                "$defs": {
                    "case": {
                        "properties": {
                            "ofen": {"minLength": 2, "type": "string"}
                        }
                    }
                }
            },
            sort_keys=True,
        ),
        suite_identity,
        "structural-alias-wrong-canonical-shape",
    )
    must_reject_structural(
        json.dumps(
            {
                "$defs": {
                    "case": {
                        "properties": {"ofen": "type:string"}
                    }
                }
            },
            sort_keys=True,
        ),
        suite_identity,
        "structural-alias-wrong-value-kind",
    )
    rationale_source = STRUCTURAL_SCALAR_ALIAS_SOURCES[-1]
    rationale_identity = {
        key: rationale_source[key]
        for key in ("path", "bytes", "sha256")
    }
    must_reject_structural(
        json.dumps(
            {
                "classifiedExistingEvidence": {
                    "positionkey": "changed rationale"
                }
            }
        ),
        rationale_identity,
        "structural-rationale-value-mutation",
    )
    must_reject_structural(
        json.dumps(
            {
                "classifiedExistingEvidence": {
                    "positions": []
                }
            }
        ),
        rationale_identity,
        "structural-container-rationale-value-kind",
    )
    must_reject_structural(
        json.dumps(
            {
                "other": {
                    "positions": (
                        "arrays of historical game OFEN strings"
                    )
                }
            }
        ),
        rationale_identity,
        "structural-container-rationale-wrong-pointer",
    )
    literal_casefold_ofens, literal_casefold_counts = (
        _lexical_scan_validated(
            '{"poſitionCount":1}',
            location="literal-unicode-casefold-position-key",
        )
    )
    escaped_casefold_ofens, escaped_casefold_counts = (
        _lexical_scan_validated(
            '{"po\\u017fitionCount":2}',
            location="escaped-unicode-casefold-position-key",
        )
    )
    if (
        literal_casefold_ofens
        or escaped_casefold_ofens
        or literal_casefold_counts != Counter({"positioncount": 1})
        or escaped_casefold_counts != Counter({"positioncount": 1})
    ):
        raise AssertionError(
            "literal and escaped keys were not Unicode case-folded"
        )
    must_reject(
        '{"poſitionCount":1,"po\\u017fitionCount":2}',
        "duplicate-unicode-casefold-key",
    )
    aliases = json.dumps(
        {
            "fen": valid_ofen,
            "CanonicalRootOfen": valid_ofen,
            "position": valid_ofen,
            "positionBefore": valid_ofen,
            "positionAfter": valid_ofen,
            "stopPosition": valid_ofen,
            "SourceOfen": valid_ofen,
            "positionKey": valid_ofen,
        }
    )
    alias_ofens, alias_key_counts = _lexical_scan_validated(
        aliases, location="vetted-scalar-aliases"
    )
    if len(alias_ofens) != 8:
        raise AssertionError("scanner missed a vetted scalar alias")
    if alias_key_counts["positionkey"] != 1:
        raise AssertionError("scanner did not count positionKey")
    partial_position_key = " ".join(valid_ofen.split()[:4])
    expected_partial_position = (
        partial_position_key
        + " "
        + " ".join(PARTIAL_OFEN_CANONICAL_SUFFIX)
    )
    partial_counts: Counter[str] = Counter()
    partial_arity_counts: Counter[tuple[str, int]] = Counter()
    partial_events: list[dict[str, Any]] = []
    partial_ofens, partial_key_counts = _lexical_scan_validated(
        json.dumps({"PoSiTiOnKeY": partial_position_key}),
        location="four-field-position-key",
        partial_ofen_canonicalization_counts=partial_counts,
        scalar_ofen_arity_counts=partial_arity_counts,
        partial_ofen_canonicalization_events=partial_events,
        source_identity=_identity(AMENDMENT_003),
    )
    if (
        partial_ofens != [expected_partial_position]
        or partial_key_counts != Counter({"positionkey": 1})
        or partial_counts != Counter({"positionkey": 1})
        or partial_arity_counts != Counter({("positionkey", 4): 1})
        or len(partial_events) != 1
        or partial_events[0]["jsonPointer"] != "/PoSiTiOnKeY"
        or partial_events[0]["field"] != "positionkey"
        or partial_events[0]["inputFieldCount"] != 4
        or partial_events[0]["outputFieldCount"] != 6
        or partial_events[0]["suffix"]
        != list(PARTIAL_OFEN_CANONICAL_SUFFIX)
    ):
        raise AssertionError(
            "scanner did not canonically expand a four-field positionKey"
        )
    arbitrary_clock_companion = " ".join(
        (*valid_ofen.split()[:4], "57", "99")
    )
    if deep._leakage_keys(
        expected_partial_position
    ) != deep._leakage_keys(arbitrary_clock_companion):
        raise AssertionError(
            "neutral legacy clocks changed conservative leakage keys"
        )
    six_field_counts: Counter[str] = Counter()
    six_field_ofens, _six_field_keys = _lexical_scan_validated(
        json.dumps({"positionKey": valid_ofen}),
        location="six-field-position-key",
        partial_ofen_canonicalization_counts=six_field_counts,
    )
    if six_field_ofens != [valid_ofen] or six_field_counts:
        raise AssertionError(
            "scanner altered or counted a six-field positionKey"
        )
    must_reject(
        json.dumps({"ofen": partial_position_key}),
        "four-field-ordinary-ofen",
    )
    malformed_ep_fields = partial_position_key.split()
    malformed_ep_fields[3] = "z99"
    must_reject(
        json.dumps({"positionKey": " ".join(malformed_ep_fields)}),
        "malformed-four-field-position-key-en-passant",
    )
    for malformed_fields in (
        partial_position_key.rsplit(" ", 1)[0],
        partial_position_key + " 0",
        expected_partial_position + " trailing",
    ):
        must_reject(
            json.dumps({"positionKey": malformed_fields}),
            "malformed-field-count-position-key",
        )
    partial_null_counts: Counter[str] = Counter()
    partial_null_expansions: Counter[str] = Counter()
    partial_null_ofens, _partial_null_keys = _lexical_scan_validated(
        '{"positionKey":null}',
        location="null-position-key",
        null_scalar_value_counts=partial_null_counts,
        partial_ofen_canonicalization_counts=partial_null_expansions,
    )
    if (
        partial_null_ofens
        or partial_null_counts != Counter({"positionkey": 1})
        or partial_null_expansions
    ):
        raise AssertionError(
            "null positionKey was not kept absent from expansion"
        )
    must_reject(
        json.dumps(
            {
                "legacyFourFieldBoardKeyCountEntries": {
                    "positionkey": 821
                }
            }
        ),
        "unsafe-position-keyed-count-map",
    )
    decoded.clear()
    globals()["_decode_json_string"] = recording
    try:
        positions_payload = json.dumps(
            {
                "positions": [
                    valid_ofen,
                    [valid_ofen],
                    {"targetCpStm": poison, "ofen": valid_ofen},
                ],
                "SourceOfens": [valid_ofen, [valid_ofen]],
                "candidatePositions": [poison],
                "normalizedOfenExact": True,
            }
        )
        selected_positions = _lexical_ofens_validated(
            positions_payload,
            location="selected-positions-container",
        )
    finally:
        globals()["_decode_json_string"] = original
    if selected_positions != [valid_ofen] * 5:
        raise AssertionError("positions container selection is incomplete")
    if poison in decoded:
        raise AssertionError(
            "positions container decoded target/metadata strings"
        )
    must_reject(
        json.dumps({"root": valid_ofen}),
        "ordinary-field-valid-raw-ofen",
    )
    unicode_ofen_token = (
        json.dumps(valid_ofen)
        .replace("/", "\\u002f")
        .replace("[", "\\u005b")
        .replace("]", "\\u005d")
    )
    must_reject(
        '{"root":' + unicode_ofen_token + "}",
        "ordinary-field-unicode-separators-ofen",
    )
    escaped_ascii_token = '"' + "".join(
        (
            f"\\u{ord(character):04x}"
            if character.isascii()
            and (
                character.isalnum()
                or character in "-/[] "
            )
            else character
        )
        for character in valid_ofen
    ) + '"'
    must_reject(
        '{"root":' + escaped_ascii_token + "}",
        "ordinary-field-all-allowed-ascii-unicode-escaped",
    )
    split_whitespace = [
        chr(codepoint)
        for codepoint in range(sys.maxunicode + 1)
        if chr(codepoint).isspace()
    ]
    if not {"\u00a0", "\u2003"}.issubset(split_whitespace):
        raise AssertionError(
            "production split whitespace set lacks NBSP or EM SPACE"
        )
    for index, whitespace in enumerate(split_whitespace):
        must_reject(
            json.dumps(
                {"root": whitespace + valid_ofen},
                ensure_ascii=False,
            ),
            f"ordinary-field-literal-split-whitespace-{index}",
        )
        must_reject(
            json.dumps(
                {"root": whitespace + valid_ofen},
                ensure_ascii=True,
            ),
            f"ordinary-field-escaped-split-whitespace-{index}",
        )
    must_reject(
        json.dumps({"root": "\t" + valid_ofen}),
        "ordinary-field-leading-json-tab",
    )
    must_reject(
        json.dumps(
            {"root": valid_ofen.replace("] w", "]\u00a0w")},
            ensure_ascii=False,
        ),
        "ordinary-field-literal-nbsp-delimiter",
    )
    must_reject(
        json.dumps(
            {"root": valid_ofen.replace("] w", "]\u2003w")},
            ensure_ascii=True,
        ),
        "ordinary-field-escaped-em-space-delimiter",
    )
    if _lexical_ofens_validated(
        json.dumps({"pv": "a0c2 j9h7 2. f1f2"}),
        location="ordinary-pv-remains-opaque",
    ):
        raise AssertionError("ordinary PV string produced an OFEN")

    def replay_projection(
        value: Any, label: str
    ) -> list[dict[str, Any]]:
        requests: list[dict[str, Any]] = []
        _lexical_scan_validated(
            json.dumps(value),
            location=label,
            replay_requests=requests,
        )
        return requests

    default_schedule = replay_projection(
        {"Openings": [{"Moves": ["e1e3", "e8e6"]}]},
        "default-opening-schedule",
    )
    if (
        len(default_schedule) != 1
        or default_schedule[0]["schema"] != "schedule-moves"
        or default_schedule[0]["initialSource"] != "official-default"
        or default_schedule[0]["containerProof"]
        != "openings-container"
        or default_schedule[0]["sourceObjectIdentity"]
        != "/Openings/0"
        or default_schedule[0]["sourceObjectOrdinal"] != 2
    ):
        raise AssertionError(
            "direct Openings-array default schedule proof drifted"
        )
    explicit_schedule = replay_projection(
        {
            "InitialOfen": valid_ofen,
            "Moves": ["e1e3", "e8e6"],
        },
        "explicit-opening-schedule",
    )
    if (
        len(explicit_schedule) != 1
        or explicit_schedule[0]["initialSource"] != "explicit"
        or explicit_schedule[0]["containerProof"] != "direct-object"
    ):
        raise AssertionError("explicit opening schedule was not projected")
    event_schedule = replay_projection(
        {
            "RecordType": "gameStart",
            "InitialOfen": valid_ofen,
            "OpeningMoves": ["e1e3", "e8e6"],
        },
        "event-opening-schedule",
    )
    if (
        len(event_schedule) != 1
        or event_schedule[0]["schema"] != "event-opening-moves"
        or event_schedule[0]["containerProof"] != "event-game-start"
    ):
        raise AssertionError("gameStart opening sequence was not projected")
    transcript = replay_projection(
        {
            "schemaVersion": 1,
            "id": "target-free",
            "initialOfen": valid_ofen,
            "moves": ["e1e3", "e8e6"],
            "positions": [valid_ofen, valid_ofen],
        },
        "transcript-replay",
    )
    if (
        len(transcript) != 1
        or transcript[0]["schema"] != "transcript"
        or transcript[0]["containerProof"] != "transcript-game"
        or transcript[0]["expectedPositions"]
        != [valid_ofen, valid_ofen]
    ):
        raise AssertionError("transcript replay projection drifted")
    for opaque_value, label in (
        (
            {"Moves": ["target-move-remains-opaque"]},
            "unproved-direct-moves",
        ),
        (
            {
                "Openings": {
                    "Moves": ["target-move-remains-opaque"]
                }
            },
            "non-array-openings-container",
        ),
        (
            {
                "Openings": [
                    {
                        "child": {
                            "Moves": ["target-move-remains-opaque"]
                        }
                    }
                ]
            },
            "nested-object-under-opening-entry",
        ),
        (
            {
                "RecordType": "ply",
                "InitialOfen": valid_ofen,
                "OpeningMoves": ["target-move-remains-opaque"],
            },
            "non-gamestart-opening-moves",
        ),
        (
            {"Pv": ["e1e3", "e8e6"]},
            "unrelated-pv-array",
        ),
    ):
        if replay_projection(opaque_value, label):
            raise AssertionError(f"opaque move array projected for {label}")
    structured_requests: list[dict[str, Any]] = []
    structured_null_counts: Counter[str] = Counter()
    structured_ofens, _structured_keys = _lexical_scan_validated(
        json.dumps(
            {
                "InitialOfen": valid_ofen,
                "OpeningMoves": [],
                "Moves": [
                    {
                        "preOfen": valid_ofen,
                        "postOfen": None,
                        "targetCp": poison,
                    }
                ],
            }
        ),
        location="opaque-structured-move-log",
        replay_requests=structured_requests,
        null_scalar_value_counts=structured_null_counts,
    )
    if (
        structured_requests
        or structured_ofens != [valid_ofen, valid_ofen]
        or structured_null_counts != Counter({"postofen": 1})
    ):
        raise AssertionError(
            "structured move log was not scanned as target-opaque evidence"
        )

    def must_reject_replay(value: Any, label: str) -> None:
        try:
            replay_projection(value, label)
        except ValueError:
            return
        raise AssertionError(f"replay projector accepted {label}")

    must_reject_replay(
        {
            "RecordType": "gameStart",
            "InitialOfen": valid_ofen,
            "Moves": [],
            "OpeningMoves": [],
        },
        "ambiguous-direct-move-arrays",
    )
    must_reject_replay(
        {"InitialOfen": valid_ofen, "Moves": [17]},
        "recognized-non-string-move",
    )
    must_reject_replay(
        {
            "InitialOfen": valid_ofen,
            "Moves": ["e1e3"],
            "Positions": [],
        },
        "transcript-count-mismatch",
    )
    _known_ofens, known_key_counts = _lexical_scan_validated(
        json.dumps(
            {
                "positionCount": 3,
                "normalizedOfenExact": True,
                "positionRole": "source",
            }
        ),
        location="known-position-metadata",
    )
    if _unknown_position_like_keys(known_key_counts):
        raise AssertionError(
            "declared position metadata was treated as unknown"
        )
    _unknown_ofens, unknown_key_counts = _lexical_scan_validated(
        '{"mysteryPositionCount":3}',
        location="unknown-position-metadata",
    )
    if _unknown_position_like_keys(unknown_key_counts) != {
        "mysterypositioncount": 1
    }:
        raise AssertionError(
            "unknown position-like key did not fail closed"
        )
    declared_classes = (
        OFEN_FIELDS,
        POSITION_CONTAINER_FIELDS,
        OPAQUE_POSITION_METADATA_FIELDS,
    )
    if any(
        left & right
        for index, left in enumerate(declared_classes)
        for right in declared_classes[index + 1 :]
    ):
        raise AssertionError(
            "position-like field classes are not disjoint"
        )
    opaque_correction_payload = json.dumps(
        {
            key: 1
            for key in sorted(
                AMENDMENT_003_OPAQUE_POSITION_METADATA_FIELDS
            )
        }
    )
    observed_opaque_payload = json.loads(opaque_correction_payload)
    observed_opaque_payload["historicalpositionroots"] = [
        "../match-runs",
        "validation",
    ]
    _opaque_ofens, opaque_key_counts = _lexical_scan_validated(
        json.dumps(observed_opaque_payload),
        location="amendment-003-opaque-metadata",
    )
    if (
        opaque_key_counts
        != Counter(
            {
                key: 1
                for key in (
                    AMENDMENT_003_OPAQUE_POSITION_METADATA_FIELDS
                )
            }
        )
        or _unknown_position_like_keys(opaque_key_counts)
    ):
        raise AssertionError(
            "amendment-003 opaque metadata classification drifted"
        )
    try:
        _lexical_scan_validated(
            json.dumps(
                {"historicalPositionRoots": [valid_ofen]}
            ),
            location="opaque-historical-root-containing-board-state",
        )
    except ValueError:
        pass
    else:
        raise AssertionError(
            "opaque historical-root metadata concealed an Omega OFEN"
        )
    stable_path = REPO / "validation" / "stable-self-test.json"
    if _require_stable_exclusion_paths(
        [stable_path], [stable_path]
    ) != 1:
        raise AssertionError("stable artifact count is incorrect")
    try:
        _require_stable_exclusion_paths(
            [stable_path],
            [stable_path, stable_path.with_name("new-self-test.json")],
        )
    except ValueError:
        pass
    else:
        raise AssertionError(
            "artifact path-set race was not rejected"
        )
    declared_roots = _prior_roots()
    if (
        len(declared_roots)
        != len(BASE_PRIOR_ROOTS) + len(AMENDMENT_003_ADDITIONAL_ROOTS)
        or not set(_amendment_003_roots()).issubset(declared_roots)
    ):
        raise AssertionError("declared prior-artifact roots drifted")
    try:
        _require_prior_root_directories(
            [REPO / ".missing-generation3-root-self-test"]
        )
    except ValueError:
        pass
    else:
        raise AssertionError("missing prior-artifact root was accepted")
    prior_files = _exclusion_files(declared_roots)
    if (
        len(prior_files) != EXPECTED_PRIOR_ARTIFACT_COUNT
        or (
            validation_mode == "frozen"
            and sum(path.stat().st_size for path in prior_files)
            != EXPECTED_PRIOR_ARTIFACT_BYTES
        )
    ):
        raise AssertionError(
            "expanded prior-artifact path/byte inventory drifted"
        )
    with tempfile.TemporaryDirectory(
        prefix="omega-managed-runtime-bundle-self-test-"
    ) as raw_runtime:
        runtime_root = Path(raw_runtime)
        synthetic_assembly = runtime_root / "Synthetic.dll"
        synthetic_assembly.write_bytes(b"synthetic assembly")
        synthetic_pin = {
            "path": str(runtime_root),
            "assemblyRelativePath": synthetic_assembly.name,
        }
        before_sidecar = _application_runtime_bundle_identity(
            synthetic_pin
        )
        (runtime_root / "Synthetic.runtimeconfig.dev.json").write_text(
            '{"runtimeOptions":{"additionalProbingPaths":["forbidden"]}}',
            encoding="utf-8",
        )
        after_sidecar = _application_runtime_bundle_identity(
            synthetic_pin
        )
        if (
            after_sidecar["fileCount"]
            != before_sidecar["fileCount"] + 1
            or after_sidecar["sha256"] == before_sidecar["sha256"]
        ):
            raise AssertionError(
                "runtimeconfig.dev.json escaped app-bundle identity"
            )
        absent_store = runtime_root / "absent-store"
        _require_absent_runtime_paths(
            {"syntheticSharedStore": absent_store}
        )
        absent_store.mkdir()
        try:
            _require_absent_runtime_paths(
                {"syntheticSharedStore": absent_store}
            )
        except ValueError:
            pass
        else:
            raise AssertionError(
                "a newly present runtime shared store was accepted"
            )
        selection_root = runtime_root / "selection"
        selected_version = selection_root / "10.0.9"
        selected_version.mkdir(parents=True)
        (selected_version / "runtime.dll").write_bytes(b"runtime")
        selection_pin = {
            "path": str(selection_root),
            "versionDirectories": ["10.0.9"],
        }
        before_empty_sibling = _directory_bundle_identity(
            selection_pin
        )
        (selection_root / "10.0.10").mkdir()
        after_empty_sibling = _directory_bundle_identity(
            selection_pin
        )
        if (
            before_empty_sibling["versionDirectories"]
            != ["10.0.9"]
            or after_empty_sibling["versionDirectories"]
            != ["10.0.10", "10.0.9"]
        ):
            raise AssertionError(
                "runtime selection version-directory inventory drifted"
            )
        try:
            _expect(
                after_empty_sibling,
                before_empty_sibling,
                "synthetic runtime version-directory inventory",
            )
        except ValueError:
            pass
        else:
            raise AssertionError(
                "empty higher runtime version directory was accepted"
            )
    injected_environment = {
        "DOTNET_ADDITIONAL_DEPS": "forbidden",
        "DOTNET_STARTUP_HOOKS": "forbidden",
        "CORE_SERVICING": "forbidden",
        "COR_ENABLE_PROFILING": "1",
        "COMPlus_ReadyToRun": "0",
        "ProgramFiles(x86)": "forbidden",
    }
    prior_environment = {
        key: os.environ.get(key) for key in injected_environment
    }
    try:
        os.environ.update(injected_environment)
        sanitized_environment = _dotnet_environment()
    finally:
        for key, prior in prior_environment.items():
            if prior is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = prior
    if any(
        key in sanitized_environment for key in injected_environment
    ):
        raise AssertionError(
            "managed subprocess retained an ambient injection variable"
        )
    if {
        key: sanitized_environment.get(key)
        for key in DOTNET_ENVIRONMENT_POLICY["setVariables"]
    } != DOTNET_ENVIRONMENT_POLICY["setVariables"]:
        raise AssertionError(
            "managed subprocess did not install the frozen environment"
        )
    helper_test = _run_pinned_dotnet(
        [str(DOTNET), str(OPENING_REPLAY_HELPER), "--self-test"],
        runtime_bundle_pin=OPENING_REPLAY_RUNTIME_BUNDLE_PIN,
        cwd=REPO,
        text=True,
        encoding="utf-8",
        errors="strict",
        capture_output=True,
        check=False,
        timeout=OPENING_REPLAY_TIMEOUT_SECONDS,
    )
    if (
        helper_test.returncode != 0
        or "self-test passed" not in helper_test.stdout
    ):
        raise AssertionError(
            "opening-prefix replay helper self-test failed: "
            f"{helper_test.stderr.strip()!r}"
        )
    canonical_root = (
        WORKSPACE
        / "corechess-arena"
        / "Tools"
        / "OmegaMatch"
        / "Openings"
    )
    (
        canonical_signatures,
        canonical_pins,
        canonical_positions,
        canonical_key_counts,
        canonical_stable_count,
        canonical_replay,
    ) = _lexical_inventory([canonical_root])
    if (
        len(canonical_pins) != 1
        or canonical_stable_count != 1
        or canonical_positions != 108
        or len(canonical_signatures) != 74
        or canonical_key_counts
        or canonical_replay["requestSchemaCounts"]
        != {"schedule-moves": 24}
        or canonical_replay["initialSourceCounts"]
        != {"official-default": 24}
        or canonical_replay["prefixRecordCount"] != 108
        or canonical_replay[
            "completeSequenceDeduplicatedPostMovePrefixOccurrenceCount"
        ]
        != 84
        or canonical_replay["uniqueOpeningPrefixIdentityCount"] != 75
        or canonical_replay[
            "uniqueOpeningPostMovePrefixIdentityCount"
        ]
        != 74
        or canonical_replay["newlyImplicitOpeningOccurrenceCount"]
        != 108
    ):
        raise AssertionError(
            "canonical opening replay integration drifted"
        )
    must_reject('{"ofen":17}', "non-string-ofen")
    must_reject(
        '{"x":true}' + " trailing", "trailing-garbage"
    )
    must_reject('{"n":01}', "invalid-leading-zero-number")
    duplicate_counts = _collision_counts(
        [valid_ofen, valid_ofen], ["same-signature", "same-signature"], [0, 0]
    )
    if (
        duplicate_counts["globalExactInputDuplicates"] != 1
        or duplicate_counts["globalConservativeOrbitDuplicates"] != 1
        or duplicate_counts["crossSplitExactInputCollisions"] != 0
        or duplicate_counts["crossSplitConservativeOrbitCollisions"] != 0
    ):
        raise AssertionError(
            "same-split exact/orbit duplicates escaped global uniqueness"
        )
    if _resolve(RULES_ROOTS) != _resolve(
        DATA_PARENT / "deep-hce-v4-random-roots.jsonl"
    ):
        raise AssertionError("rules-only path drift")
    if _resolve(DATA_DIR) != _resolve(DATA_PARENT / "deep-hce-v4"):
        raise AssertionError("generation directory drift")
    if (
        len(GENERATION3_DESTINATION_SUBTREES) != 4
        or len(
            {
                _resolve(path)
                for path in GENERATION3_DESTINATION_SUBTREES
            }
        )
        != 4
    ):
        raise AssertionError(
            "fresh generation destination inventory drifted"
        )
    for destination in GENERATION3_DESTINATION_SUBTREES:
        expected_destination = _resolve(destination)
        descendant = expected_destination / "synthetic-output.json"
        if (
            _generation3_destination_for(expected_destination)
            != expected_destination
            or _generation3_destination_for(descendant)
            != expected_destination
        ):
            raise AssertionError(
                "fresh generation destination containment drifted"
            )
        try:
            _exclusion_files([descendant])
        except ValueError:
            pass
        else:
            raise AssertionError(
                "fresh generation destination was accepted as history"
            )
    _require_fresh_destinations_absent_or_empty()
    for fresh in FRESH_SOURCE_ARTIFACTS:
        if fresh in _exclusion_files([DATA_PARENT]):
            raise AssertionError(
                "fresh rules-only source entered prior exclusion inventory"
            )
    selected = generation3_trainer._select_checkpoint_epoch(
        [(36, -100.0), (37, 1.0), (38, 1.0), (48, 2.0)]
    )
    if selected != 37:
        raise AssertionError("exact QAT tie did not retain earliest epoch")
    if (
        generation3_trainer._select_checkpoint_epoch(
            [(37, 2.0), (48, 1.0)]
        )
        != 48
    ):
        raise AssertionError("QAT minimum selection is incorrect")

    def must_raise_value_error(
        operation: Callable[[], Any], label: str
    ) -> None:
        try:
            operation()
        except ValueError:
            return
        raise AssertionError(f"{label} did not fail closed")

    _validate_strict_utc(
        "2026-07-19T12:34:56.123456Z",
        label="self-test timestamp",
    )
    for invalid_timestamp in (
        "2026-07-19T12:34:56+00:00",
        "2026-07-19 12:34:56Z",
        "2026-02-30T12:34:56Z",
        "2026-07-19T12:34Z",
    ):
        must_raise_value_error(
            lambda value=invalid_timestamp: _validate_strict_utc(
                value, label="invalid self-test timestamp"
            ),
            f"strict timestamp {invalid_timestamp!r}",
        )
    _require_five_digit_amendment_size({"bytes": 10000})
    _require_five_digit_amendment_size({"bytes": 99999})
    for invalid_bytes in (9999, 100000):
        must_raise_value_error(
            lambda value=invalid_bytes: (
                _require_five_digit_amendment_size({"bytes": value})
            ),
            f"five-digit amendment envelope {invalid_bytes}",
        )

    protocol = _load_json_strict(
        MATCH_PROTOCOL, "self-test match protocol"
    )
    adapter = _load_json_strict(
        MATCH_ADAPTER, "self-test match adapter"
    )
    _validate_match_declaration_transaction(
        AMENDMENT_003_PIN,
        protocol=protocol,
        adapter=adapter,
    )
    reversed_protocol = copy.deepcopy(protocol)
    reversed_protocol["amendmentChain"] = list(
        reversed(reversed_protocol["amendmentChain"])
    )
    must_raise_value_error(
        lambda: _validate_match_declaration_transaction(
            AMENDMENT_003_PIN,
            protocol=reversed_protocol,
            adapter=adapter,
        ),
        "reversed protocol amendment chain",
    )
    duplicate_adapter = copy.deepcopy(adapter)
    duplicate_adapter["declarations"]["amendmentChain"][2] = (
        copy.deepcopy(
            duplicate_adapter["declarations"]["amendmentChain"][1]
        )
    )
    must_raise_value_error(
        lambda: _validate_match_declaration_transaction(
            AMENDMENT_003_PIN,
            protocol=protocol,
            adapter=duplicate_adapter,
        ),
        "duplicate adapter amendment chain",
    )
    stale_protocol = copy.deepcopy(protocol)
    stale_protocol["amendmentChain"][2]["bytes"] -= 1
    must_raise_value_error(
        lambda: _validate_match_declaration_transaction(
            AMENDMENT_003_PIN,
            protocol=stale_protocol,
            adapter=adapter,
        ),
        "stale protocol amendment identity",
    )
    wrong_protocol_adapter = copy.deepcopy(adapter)
    wrong_protocol_adapter["compatibility"]["protocol"] = (
        "validation/not-the-canonical-protocol.json"
    )
    must_raise_value_error(
        lambda: _validate_match_declaration_transaction(
            AMENDMENT_003_PIN,
            protocol=protocol,
            adapter=wrong_protocol_adapter,
        ),
        "wrong adapter protocol path",
    )

    stable_state, stable_passes, stable_fingerprint = (
        _fixed_point_until_two_complete_states(
            0,
            lambda state: (
                1,
                state == 1,
                "stable-fingerprint",
            ),
            maximum_passes=4,
        )
    )
    if (
        stable_state != 1
        or stable_passes != 3
        or stable_fingerprint != "stable-fingerprint"
    ):
        raise AssertionError("two-complete-state fixed point drifted")
    must_raise_value_error(
        lambda: _fixed_point_until_two_complete_states(
            0,
            lambda state: (
                state + 1,
                True,
                f"changing-{state}",
            ),
            maximum_passes=4,
        ),
        "nonconvergent fixed point",
    )

    draft_document = _load_json_strict(
        AMENDMENT_003, "self-test draft amendment"
    )
    neutral_candidate = copy.deepcopy(draft_document)
    neutral_candidate["status"] = FROZEN_AMENDMENT_003_STATUS
    neutral_candidate["createdUtc"] = "2026-07-19T12:34:56Z"
    neutral_candidate["classifiedExistingEvidence"][
        "openingAndTranscriptReplay"
    ]["nullScalarValueCountEntries"] = [
        {
            "field": "postofen",
            "occurrencesDecimal": "00000000000000000002",
        }
    ]
    if _document_target_free_contribution(
        AMENDMENT_003, neutral_candidate
    ) != _document_target_free_contribution(
        AMENDMENT_003, draft_document
    ):
        raise AssertionError(
            "target-neutral declaration scalar substitution drifted"
        )
    unsafe_null_map = copy.deepcopy(draft_document)
    unsafe_null_map["classifiedExistingEvidence"][
        "openingAndTranscriptReplay"
    ]["nullScalarValueCounts"] = {"postofen": 1}
    must_raise_value_error(
        lambda: _document_target_free_contribution(
            AMENDMENT_003, unsafe_null_map
        ),
        "unsafe recursive null-scalar evidence map",
    )

    with tempfile.TemporaryDirectory(
        prefix="omega-v3-projection-resume-selftest-"
    ) as temporary:
        projection = Path(temporary) / "projection.jsonl"
        rows = [
            {
                "groupId": "group-a",
                "ofen": OMEGA_INITIAL_OFEN,
                "phase": "opening",
            }
        ]
        _write_projection(projection, rows)
        first_identity = _identity(projection)
        _write_projection(projection, rows)
        if _identity(projection) != first_identity:
            raise AssertionError(
                "idempotent projection verification changed the artifact"
            )
        projection.write_bytes(b"foreign projection\n")
        foreign_identity = _identity(projection)
        must_raise_value_error(
            lambda: _write_projection(projection, rows),
            "conflicting resumed projection",
        )
        if _identity(projection) != foreign_identity:
            raise AssertionError(
                "conflicting resumed projection was modified"
            )

    with tempfile.TemporaryDirectory(
        prefix="omega-v3-final-rehash-selftest-"
    ) as temporary:
        toctou = Path(temporary) / "source.json"
        toctou.write_text("{}\n", encoding="utf-8")
        expected_toctou = [_identity(toctou)]
        toctou.write_text('{"changed":true}\n', encoding="utf-8")
        must_raise_value_error(
            lambda: _final_identity_pass(
                [toctou],
                expected_toctou,
                label="self-test-toctou",
            ),
            "final identity TOCTOU",
        )
    print("king_state_v3 self-test passed")


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    proposal = subparsers.add_parser(
        "propose-amendment-003-freeze",
        help=(
            "read-only target-free inventory scan and in-memory fixed-point "
            "freeze proposal"
        ),
    )
    proposal.add_argument("--created-utc", required=True)
    proposal.add_argument(
        "--output",
        type=Path,
        help=(
            "optional create-new proposal path outside the shared workspace; "
            "stdout is used by default"
        ),
    )
    subparsers.add_parser(
        "verify-amendment-003",
        help=(
            "run two fresh read-only end-to-end verifiers and require "
            "identical complete states"
        ),
    )
    subparsers.add_parser(
        "resolve-initializer",
        help="resolve K2 priority 1, otherwise exact K0, before labels",
    )
    subparsers.add_parser(
        "sample", help="generate the canonical target-free rules-only roots"
    )
    subparsers.add_parser(
        "prepare", help="freeze the exact rules-only candidate pool"
    )
    subparsers.add_parser(
        "audit-freeze",
        help="publish exclusion, incidence, feature, bucket, and orbit gates",
    )
    subparsers.add_parser(
        "seal", help="publish the no-clobber pre-label compatibility seal"
    )
    subparsers.add_parser(
        "verify-seal", help="rehash and recompute every pre-label gate"
    )
    run = subparsers.add_parser(
        "run", help="run teacher searches only after strict seal verification"
    )
    run.add_argument("--jobs", type=int, default=1)
    run.add_argument("--limit", type=int)
    run.add_argument("--timeout-seconds", type=float, default=180.0)
    subparsers.add_parser(
        "finalize", help="finalize paired search labels and audit immediately"
    )
    subparsers.add_parser(
        "audit-finalized",
        help="target-opaque audit of the finalized search corpus",
    )
    subparsers.add_parser(
        "build-residual",
        help="label static HCE, build residuals, and verify target-opaquely",
    )
    subparsers.add_parser(
        "verify-corpus",
        help="publish/verify residual, feature, phase, and preclaim gates",
    )
    subparsers.add_parser(
        "self-test", help="run target-free lexical and policy tests"
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.command == "propose-amendment-003-freeze":
        _propose_amendment_003_freeze(
            created_utc=args.created_utc,
            output=args.output,
        )
    elif args.command == "verify-amendment-003":
        _verify_amendment_003()
    elif args.command == "resolve-initializer":
        _resolve_initializer()
    elif args.command == "sample":
        _sample()
    elif args.command == "prepare":
        _prepare()
    elif args.command == "audit-freeze":
        _audit_freeze()
    elif args.command == "seal":
        _seal()
    elif args.command == "verify-seal":
        report = _verify_seal()
        print(
            f"Verified generation-3 seal for {report['initializer']} "
            f"({report['compatibilityIdentityCount']} pinned identities)."
        )
    elif args.command == "run":
        _run(
            jobs=args.jobs,
            limit=args.limit,
            timeout=args.timeout_seconds,
        )
    elif args.command == "finalize":
        _finalize()
    elif args.command == "audit-finalized":
        _audit_finalized()
    elif args.command == "build-residual":
        _build_residual()
    elif args.command == "verify-corpus":
        _verify_corpus()
    elif args.command == "self-test":
        _self_test()
    else:
        raise AssertionError(args.command)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
