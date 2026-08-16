import LearnerSubjectPage from '@/components/personalization/LearnerSubjectPage'

export default function SubjectLearningModelSettingsPage({
  params,
}: {
  params: Promise<{ subjectId: string }>
}) {
  return <LearnerSubjectPage params={params} />
}
