import {studies,runs,rollBatches,rollRuns} from '../api/research.js';
export const store={studies:[],runs:[],studyId:0,runId:0,rows:[],rollBatches:[],rollRuns:[],rollBatchId:0,rollRunId:0,rollRows:[]};
export async function load(){[store.studies,store.runs,store.rollBatches,store.rollRuns]=await Promise.all([studies.list(),runs.list(),rollBatches.list(),rollRuns.list()]);if(!store.studyId)store.studyId=store.studies[0]?.id||0;if(!store.rollBatchId)store.rollBatchId=store.rollBatches[0]?.id||0;}
export async function loadRows(){store.rows=store.studyId?await studies.rows(store.studyId):[];}
export async function loadRollRows(){store.rollRows=store.rollBatchId?await rollBatches.records(store.rollBatchId):[];}
