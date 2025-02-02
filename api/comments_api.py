# -*- coding: utf-8 -*-
# Copyright 2021 Google Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License")
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import logging

from framework import basehandlers
from framework import permissions
from internals import approval_defs
from internals import review_models
from internals import notifier


def comment_to_json_dict(comment):

  return {
      'comment_id': comment.key.id(),
      'feature_id': comment.feature_id,
      'field_id': comment.field_id,
      'created': str(comment.created),  # YYYY-MM-DD HH:MM:SS.SSS
      'author': comment.author,
      'content': comment.content,
      'deleted_by': comment.deleted_by,
      'old_approval_state': comment.old_approval_state,
      'new_approval_state': comment.new_approval_state,
      }


class CommentsAPI(basehandlers.APIHandler):
  """Users may see the list of comments on one of the approvals of a feature,
   and add their own, if allowed."""

  def _should_show_comment(self, comment, email, is_admin):
    """Check whether a comment should be visible to the user."""
    return comment.deleted_by is None or email == comment.deleted_by or is_admin

  def do_get(self, feature_id, field_id=None):
    """Return a list of all review comments on the given feature."""
    # Note: We assume that anyone may view approval comments.
    comments = review_models.Comment.get_comments(feature_id, field_id)
    user = self.get_current_user(required=True)
    is_admin = permissions.can_admin_site(user)
    
    # Filter deleted comments the user can't see.
    comments = filter(
      lambda c: self._should_show_comment(c, user.email(), is_admin), comments)

    dicts = [comment_to_json_dict(c) for c in comments]
    data = {
        'comments': dicts,
        }
    return data

  def do_post(self, feature_id, field_id=None):
    """Add a review comment and possibly set a approval value."""
    new_state = self.get_int_param(
        'state', required=False,
        validator=review_models.Approval.is_valid_state)
    feature = self.get_specified_feature(feature_id=feature_id)
    user = self.get_current_user(required=True)
    post_to_approval_field_id = self.get_param(
        'postToApprovalFieldId', required=False)

    old_state = None
    if field_id is not None and new_state is not None:
      old_approvals = review_models.Approval.get_approvals(
          feature_id=feature_id, field_id=field_id,
          set_by=user.email())
      if old_approvals:
        old_state = old_approvals[0].state

      approvers = approval_defs.get_approvers(field_id)
      if not permissions.can_approve_feature(user, feature, approvers):
        self.abort(403, msg='User is not an approver')
      review_models.Approval.set_approval(
          feature.key.integer_id(), field_id, new_state, user.email())

    comment_content = self.get_param('comment', required=False)

    if comment_content or new_state is not None:
      comment = review_models.Comment(
          feature_id=feature_id, field_id=field_id,
          author=user.email(), content=comment_content,
          old_approval_state=old_state,
          new_approval_state=new_state)
      comment.put()

      # Schema migration double-write.
      comment_activity = review_models.Activity(
        id=comment.key.integer_id(), feature_id=feature_id, gate_id=field_id,
        author=user.email(), content=comment_content)
      comment_activity.put()

    if post_to_approval_field_id:
      notifier.post_comment_to_mailing_list(
          feature, post_to_approval_field_id, user.email(), comment_content)

    # Callers don't use the JSON response for this API call.
    return {'message': 'Done'}

  def do_patch(self, feature_id):
    comment_id = self.get_param('commentId', required=True)
    comment = review_models.Comment.get_by_id(comment_id)
    comment_activity = review_models.Activity.get_by_id(comment_id)

    user = self.get_current_user(required=True)
    if not permissions.can_admin_site(user) and user.email() != comment.author:
      self.abort(403, msg='User does not have comment edit permissions')

    is_undelete = self.get_param('isUndelete', required=True)
    if is_undelete:
      comment.deleted_by = None
      if comment_activity is not None:
        comment_activity.deleted_by = None
        comment_activity.put()
    else:
      comment.deleted_by = user.email()
      if comment_activity is not None:
        comment_activity.deleted_by = user.email()
        comment_activity.put()
    comment.put()

    return {'message': 'Done'}
